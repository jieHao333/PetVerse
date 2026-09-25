# =============================================================
# 将本目录下的 *.yml 配置导入 Nacos 配置中心
# dataId = 文件名去掉 .yml 后缀（如 shop-service.yml -> dataId=shop-service），group=DEFAULT_GROUP，格式=yaml
# 用法：在 nacos-config 目录执行  powershell -ExecutionPolicy Bypass -File .\push-to-nacos.ps1
# 可通过环境变量覆盖：
#   NACOS_ADDR      Nacos 地址（默认 localhost:8848）
#   NACOS_GROUP     分组（默认 DEFAULT_GROUP）
#   NACOS_NAMESPACE 命名空间 id（public 留空；自定义命名空间填其 id）
# =============================================================
$ErrorActionPreference = "Stop"
$addr   = if ($env:NACOS_ADDR)      { $env:NACOS_ADDR }      else { "localhost:8848" }
$group  = if ($env:NACOS_GROUP)     { $env:NACOS_GROUP }     else { "DEFAULT_GROUP" }
$tenant = if ($env:NACOS_NAMESPACE) { $env:NACOS_NAMESPACE } else { "" }
$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path

Get-ChildItem -Path $dir -Filter *.yml | ForEach-Object {
    $dataId = $_.BaseName          # 去掉 .yml 后缀作为 dataId
    $file   = $_.FullName
    Write-Host "==> publishing dataId=$dataId group=$group namespace=$(if($tenant){$tenant}else{'public'})"
    curl.exe -s -X POST "http://$addr/nacos/v1/cs/configs" `
        --data-urlencode "dataId=$dataId" `
        --data-urlencode "group=$group" `
        --data-urlencode "tenant=$tenant" `
        --data-urlencode "type=yaml" `
        --data-urlencode "content@$file"
    Write-Host ""
}
Write-Host "All configs published to Nacos at $addr"
