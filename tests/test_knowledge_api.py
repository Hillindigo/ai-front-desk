"""Phase F F4 测试：版本化知识 API 与演示身份边界（真实 ASGI 栈 + Fake 模式）。

覆盖：
- 创建草稿 -> 列表/详情/更新 -> 预览 -> 发布 -> 检索 -> 归档 的 HTTP 闭环。
- 错误码：INVALID_INPUT(422)、KNOWLEDGE_NOT_FOUND(404)、INVALID_STATE_TRANSITION(409)。
- 身份边界：user_id 与已解析身份不一致 -> 403。
- 响应不返回 embedding、Prompt 或供应商原始响应。
- 刷新状态/重建索引接口。
"""

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def _mk_doc(client, title="测试接口条目", content="门店周五休息半天", category="营业时间",
            keywords=None, user_id="default_user"):
    resp = client.post("/api/v1/knowledge/documents", json={
        "title": title, "content": content, "category": category,
        "keywords": keywords or ["周五"], "user_id": user_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestDocumentCRUD:
    def test_创建草稿并查询(self, client):
        doc = _mk_doc(client)
        assert doc["status"] == "draft"
        assert "document_id" in doc
        did = doc["document_id"]

        listed = client.get(f"/api/v1/knowledge/documents?status=draft&keyword=周五")
        assert listed.status_code == 200
        body = listed.json()
        assert any(x["document_id"] == did for x in body["items"])

        detail = client.get(f"/api/v1/knowledge/documents/{did}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["title"] == "测试接口条目"
        # 不泄漏 embedding / prompt
        assert "embedding" not in data
        assert "prompt" not in str(data).lower()

    def test_更新草稿(self, client):
        doc = _mk_doc(client, title="待改", content="原始内容", category="政策")
        did = doc["document_id"]
        up = client.put(f"/api/v1/knowledge/documents/{did}", json={
            "content": "更新后的内容", "title": "已改",
        })
        assert up.status_code == 200
        assert up.json()["content"] == "更新后的内容"

    def test_空内容创建返回422(self, client):
        resp = client.post("/api/v1/knowledge/documents", json={
            "title": "x", "content": "   ", "category": "c",
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "INVALID_INPUT"

    def test_不存在返回404(self, client):
        resp = client.get("/api/v1/knowledge/documents/999999")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "KNOWLEDGE_NOT_FOUND"

    def test_身份不一致返回403(self, client):
        resp = client.get("/api/v1/knowledge/documents?user_id=someone_else")
        assert resp.status_code == 403


class TestPublishViaAPI:
    def test_发布检索归档闭环(self, client):
        doc = _mk_doc(client, title="价格条目", content="肩颈放松特惠价88元",
                      category="服务项目", keywords=["肩颈", "特惠"])
        did = doc["document_id"]

        # 预览（草稿可作为候选检索，标记 preview）
        prev = client.post(f"/api/v1/knowledge/documents/{did}/preview", json={
            "query": "肩颈", "top_k": 5,
        })
        assert prev.status_code == 200
        assert prev.json()["preview"] is True

        # 发布
        pub = client.post(f"/api/v1/knowledge/documents/{did}/publish")
        assert pub.status_code == 200
        result = pub.json()
        assert result["status"] == "published"
        assert "knowledge_version" in result and "source_version" in result

        # 管理检索预览接口
        sp = client.post("/api/v1/knowledge/search/preview", json={
            "query": "肩颈", "top_k": 5, "include_draft_ids": [],
        })
        assert sp.status_code == 200

        # 归档
        arc = client.post(f"/api/v1/knowledge/documents/{did}/archive")
        assert arc.status_code == 200
        assert arc.json()["status"] == "archived"
        # 幂等归档
        arc2 = client.post(f"/api/v1/knowledge/documents/{did}/archive")
        assert arc2.json()["status"] == "archived"

    def test_发布非草稿返回409(self, client):
        doc = _mk_doc(client, title="已发布", content="内容", category="c")
        did = doc["document_id"]
        client.post(f"/api/v1/knowledge/documents/{did}/publish")
        # 已发布再发布 -> 409
        resp = client.post(f"/api/v1/knowledge/documents/{did}/publish")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INVALID_STATE_TRANSITION"


class TestRefreshAPI:
    def test_刷新状态与重建(self, client):
        st = client.get("/api/v1/knowledge/refresh")
        assert st.status_code == 200
        body = st.json()
        assert "status" in body and "source_version" in body
        assert body["multi_process"] is False

        rebuild = client.post("/api/v1/knowledge/refresh")
        assert rebuild.status_code == 200
