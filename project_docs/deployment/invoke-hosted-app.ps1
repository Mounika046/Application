param(
    [Parameter(Mandatory = $true)]
    [string]$TokenUrl,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [Parameter(Mandatory = $true)]
    [string]$ClientSecret,

    [Parameter(Mandatory = $true)]
    [string]$Scope,

    [Parameter(Mandatory = $true)]
    [string]$EndpointUrl,

    [Parameter(Mandatory = $true)]
    [string]$UserInput,

    [string]$ConversationId = "",

    [switch]$ReturnState
)

$tokenResponse = Invoke-RestMethod `
    -Method Post `
    -Uri $TokenUrl `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{
        grant_type = "client_credentials"
        client_id = $ClientId
        client_secret = $ClientSecret
        scope = $Scope
    }

$accessToken = [string]$tokenResponse.access_token
if (-not $accessToken) {
    throw "Could not fetch access token."
}

$body = @{
    user_input = $UserInput
    conversation_id = ($(if ($ConversationId) { $ConversationId } else { $null }))
    return_state = [bool]$ReturnState
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
    -Method Post `
    -Uri $EndpointUrl `
    -Headers @{ Authorization = "Bearer $accessToken" } `
    -ContentType "application/json" `
    -Body $body
