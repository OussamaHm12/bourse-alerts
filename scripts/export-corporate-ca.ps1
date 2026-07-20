# Export the corporate TLS-inspection CA chain so build containers can validate
# HTTPS through the Forcepoint proxy. These certificates are already trusted by
# this machine's OS store; this only makes a container agree with its host.
$parts = @()
Get-ChildItem Cert:\LocalMachine\Root |
    Where-Object { $_.Subject -match 'Forcepoint|Chaabi|Banque Centrale Populaire' } |
    ForEach-Object {
        $b64 = [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks')
        $parts += "-----BEGIN CERTIFICATE-----"
        $parts += $b64
        $parts += "-----END CERTIFICATE-----"
    }
$parts -join "`n" | Out-File -FilePath 'C:\tmp\ca\corporate-ca.crt' -Encoding ascii
Write-Output ("exported {0} certificates" -f ($parts.Count / 3))
