# Hermes Search Stack (PVE CT 121)

## Overview

Self-hosted search with automatic fallback to Tavily API.

| Component | Port | Access |
|-----------|------|--------|
| SearXNG | 8081 | http://100.126.92.41:8081 |
| Redis | 6389 | Internal only |
| Cloudflare Tunnel | - | search.thebakkens.net (pending DNS) |

## Deployment

**Container**: CT 121 (PVE Physical Server)  
**RAM**: 24GB allocated

### Services

1. **SearXNG**: Meta-search aggregator (Google, Bing, Wikipedia, etc.)
2. **Redis**: Result caching
3. **Omniroute**: Routing service

## Configuration

### SearXNG Settings

Location: `/opt/search-stack/settings.yml`

To add/disable engines, edit the `engines:` section.

### Cloudflare Tunnel

Location: `/root/.cloudflared/config.yml`

```yaml
tunnel: 6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670
credentials-file: /root/.cloudflared/6f7bfd77-ae71-40fd-bc8b-cb3f1e50d670.json
ingress:
  - hostname: omniroute.thebakkens.net
    service: http://localhost:20128
  - hostname: search.thebakkens.net
    service: http://localhost:8081
  - service: http_status:404
```

## Hermes Integration

Run the MCP server on PVE:

```bash
cd /opt/search-stack
python3 search-mcp-server.py
```

Environment variables:

- `SEARXNG_URL`: http://localhost:8081 (default)
- `TAVILY_API_KEY`: Your Tavily API key (for fallback)

### Example Usage

```bash
export SEARXNG_URL=http://100.126.92.41:8081
export TAVILY_API_KEY=your-key
python3 search-mcp-server.py
```

## API Endpoints

### Direct SearXNG

```bash
curl "http://100.126.92.41:8081/search?q=test&format=json"
```

### Cloudflare Tunnel

```bash
curl "https://search.thebakkens.net/search?q=test&format=json"
```

## Notes

- SearXNG binds to `0.0.0.0:8081` (accessible via Tailscale)
- Cloudflare tunnel routes `search.thebakkens.net` to SearXNG
- Tavily fallback triggers when local search times out or returns empty results