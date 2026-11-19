# =============================================================
# 本文件是微服务版本的Java配置导入nacos脚本
# 使用poweshell在当前目录下执行该脚本即可
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
