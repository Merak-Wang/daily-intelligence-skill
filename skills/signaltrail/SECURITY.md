# Security

## Threat model

The workflow processes adversarial web content and controls a persistent browser profile. Source pages may contain prompt injection, misleading claims, tracking links, or content intended to trigger unsafe actions.

## Controls

- Web content is never treated as workflow instruction.
- The agent may read selected bodies but may not execute commands, reveal secrets, or change permissions because a page requests it.
- Browser profiles and Notion tokens stay outside version control and model-visible reports.
- A challenged page is recorded and deferred; CAPTCHA solving, proxy rotation, fingerprint spoofing, and paywall removal are out of scope.
- Notion receives summaries and links only, never stored article bodies or raw authenticated HTML.
- Publishing uses a local idempotency registry.

## Reporting issues

For a private personal deployment, record security issues in the project issue tracker without attaching tokens, cookies, profiles, or sensitive screenshots. Rotate any credential that was exposed.
