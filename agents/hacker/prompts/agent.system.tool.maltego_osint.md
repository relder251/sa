# Tool: maltego_osint

Run OSINT transforms via the Maltego service to enumerate targets.

## Usage

```json
{
  "tool_name": "maltego_osint",
  "tool_args": {
    "transform": "<transform_name>",
    "value": "<target_value>"
  }
}
```

## Available Transforms

| Transform | Input | Returns |
|-----------|-------|---------|
| DomainToIPAddress | domain | A records |
| DomainToDNSRecords | domain | All DNS records |
| DomainToSubdomains | domain | Subdomain enumeration |
| DomainToWhois | domain | WHOIS registration data |
| IPToGeolocation | IP address | Geo coordinates, country, ASN |
| IPToASN | IP address | Autonomous System info |
| IPToShodan | IP address | Open ports, banners |
| EmailToDomain | email | Associated domain |
| EmailToBreaches | email | Known data breaches |
| DomainToCompanyProfile | domain | Company/org metadata |

## Examples

```json
{"tool_name": "maltego_osint", "tool_args": {"transform": "DomainToIPAddress", "value": "example.com"}}
{"tool_name": "maltego_osint", "tool_args": {"transform": "IPToShodan", "value": "93.184.216.34"}}
{"tool_name": "maltego_osint", "tool_args": {"transform": "EmailToBreaches", "value": "user@example.com"}}
```
