# Authentication & User Management Planning

## Current State
- Using `htpasswd` files for basic access control
- Two roles: `internal` and `admin`
- Access control at layer/asset level via `access` arrays in config
- Simple password-based authentication
- Hard to track individual usage or change access rules

## Recommended Approach: OAuth2/OpenID Connect

### Why This Makes Sense
- **Minimal Code Changes:** Existing role logic stays intact
- **No User Management:** Enterprise handles user provisioning  
- **SSO Ready:** Users get single sign-on across systems
- **Scalable:** Can easily add more providers later
- **Standards-Based:** Well-documented, lots of examples

### Implementation Complexity
**Estimated Work: 2-3 days**

**Backend Changes:**
- Add OAuth2 middleware to FastAPI app (~50 lines)
- Use `python-jose` for JWT token validation
- Keep existing role-based access control logic

**Frontend Changes:**
- Add login/logout buttons to HTML templates (~100 lines)
- Store JWT tokens in localStorage
- Include tokens in API requests

### What Stays the Same
- `access` arrays in configs
- Layer/asset permission logic
- Client-side map functionality
- Python backend operations

### What Changes
- Authentication method (OAuth2 instead of htpasswd)
- API calls include authorization headers
- Frontend shows user info and logout options

## Alternative Options

### AWS Cognito Integration
- **Work:** 3-4 days
- Integrates with existing AWS CDK setup
- Built-in user management if needed later
- Can sync with enterprise directories

### Minimal Custom Auth
- **Work:** 4-5 days
- Replace htpasswd with simple user store (JSON/SQLite)
- Add JWT token generation/validation
- Keep existing role logic

## Key Insight
The existing permission system is already well-designed - you just need to plug in modern authentication instead of the htpasswd files. OAuth2 would let you keep 95% of your current code while adding enterprise-grade authentication.

## Next Steps
When ready to implement:
1. Choose OAuth2 provider (Google, Azure AD, Okta, etc.)
2. Set up OAuth2 application credentials
3. Implement backend middleware
4. Update frontend authentication flow
5. Test with existing role system

---
*This document captures the key points from our initial planning discussion. The OAuth2 approach appears to be the sweet spot of minimal complexity + maximum benefit.*
