$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    throw '找不到 Python，请先安装 Python 3.10+ 或创建 .venv'
}

Write-Host '生成可编辑 Excel 与规范化演示数据...'
& $python (Join-Path $root 'scripts\generate_demo_excel.py')

$processes = @(
    @{ Kind = 'massage'; Role = 'buyer'; Port = 8101; Url = 'http://127.0.0.1:8101/' },
    @{ Kind = 'massage'; Role = 'admin'; Port = 8102; Url = 'http://127.0.0.1:8102/admin/login' },
    @{ Kind = 'beauty'; Role = 'buyer'; Port = 8201; Url = 'http://127.0.0.1:8201/' },
    @{ Kind = 'beauty'; Role = 'admin'; Port = 8202; Url = 'http://127.0.0.1:8202/admin/login' }
)

foreach ($item in $processes) {
    Write-Host ("启动 {0} {1}: {2}" -f $item.Kind, $item.Role, $item.Url)
    Start-Process -FilePath $python `
        -ArgumentList @('scripts\run_demo.py', $item.Kind, $item.Role, '--port', $item.Port) `
        -WorkingDirectory $root `
        -PassThru | Out-Null
}

Write-Host '已启动四个相互隔离的进程：'
Write-Host '按摩买家: http://127.0.0.1:8101/'
Write-Host '按摩后台: http://127.0.0.1:8102/admin/login'
Write-Host '美容买家: http://127.0.0.1:8201/'
Write-Host '美容后台: http://127.0.0.1:8202/admin/login'
