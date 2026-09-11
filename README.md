# NYSFIRETOOLS Fire Training Scheduler

An independent course and instructor coordination system for participating New York State counties and instructors assigned to the New York State Academy of Fire Science.

This is an independent NYSFIRETOOLS beta project. It is not an official New York State or State Fire system and has not been formally approved or endorsed.

## Current product slice

- Statewide dashboard and schedule awareness
- Course library with a persistent Course Record Number and instructor staffing requirements
- Statewide instructor directory covering all 62 New York counties and the Academy
- One NYSFIRETOOLS account for protected tools, with optional Fire Training Scheduler enrollment
- Separate general-access and Scheduler signup paths, plus later Scheduler enrollment from an existing account
- State, county, academy, and department/agency authority levels
- Department directory seeded from New York Open Data dataset `qfsu-zcpv`, with county-scoped duplicate protection
- Safe agency-merger workflow that transfers current relationships while preserving historical training records and former-name aliases
- Course-specific instructor authorizations
- Instructor self-registration with Site Administrator account approval
- Required SFI, CFI, or MFI number during self-registration, optional for administrator-created records
- Possible-profile matching and explicit merge during application approval
- Login-only Site Administrator accounts and scheduling-only instructor records
- Instructor-submitted course authorization claims with State verification
- Statewide user management and county/organization administrator assignments
- Site Administrator management of participating counties and organizations
- Case-insensitive unique account emails and duplicate-account consolidation
- Self-service password recovery and Site Administrator password resets
- Email notifications for active, linked instructor accounts covering assignments, removals, schedule changes, cancellations, account approval, and authorization approval
- Instructor-controlled notification preferences with explicit text-message consent
- Twilio SMS delivery with auditable delivery history
- Instructor travel preferences
- Instructor-entered preferred, tentative, and unavailable time windows
- Monday–Sunday availability view with visible daily time windows
- Repeating weekly availability rules with optional end dates
- Purposed, confirmed, completed, and cancelled training
- Unique Course Offering Numbers required before confirmation
- Multi-session training dates
- Hard instructor double-booking validation
- Qualified-instructor matching
- County-scoped administrator permissions
- Optional external Acadis registration link

Student registration, rosters, LMS features, and Acadis data synchronization are intentionally out of scope.

## Interface completion checklist

Treat interface work as complete only after checking both a standard desktop viewport and a 390-pixel phone viewport. Verify shared component alignment, readable wrapping, keyboard focus, touch-target sizing, and the empty, short, and long-content states before deployment.

## Local setup

1. Create and activate a Python 3.14 virtual environment.
2. Install `requirements.txt`.
3. Run `python manage.py migrate`.
4. Optionally run `python manage.py seed_demo` for demonstration data.
5. Run `python manage.py runserver`.

SQLite is used locally when `DATABASE_URL` is absent. Railway supplies PostgreSQL through `DATABASE_URL` in production.

## Railway

Create a Railway project with an application service and PostgreSQL service. Configure:

- `DEBUG=false`
- `SECRET_KEY` as a long random value
- `ALLOWED_HOSTS=scheduling.nysfiretools.com,<railway-generated-host>`
- `CSRF_TRUSTED_ORIGINS=https://scheduling.nysfiretools.com,https://<railway-generated-host>`
- `DATABASE_URL` as a reference to the Railway PostgreSQL service
- `EMAIL_BACKEND=config.email_backend.CloudflareEmailBackend`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_EMAIL_API_TOKEN`, and `DEFAULT_FROM_EMAIL` for Cloudflare password-reset email delivery over HTTPS
- `NOTIFICATION_EMAIL_ENABLED=true` to enable operational email notices
- `SITE_BASE_URL=https://scheduling.nysfiretools.com` for links in notifications
- `NYSFIRETOOLS_MAIN_ORIGIN=https://www.nysfiretools.com` and the same long random `NYSFIRETOOLS_SSO_CLIENT_SECRET` used by the main site for shared account sign-in
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and either `TWILIO_MESSAGING_SERVICE_SID` or `TWILIO_FROM_NUMBER` for opt-in texts

The included Dockerfile runs migrations and starts Gunicorn. The Railway health check uses `/health/`.

After deployment succeeds, add `scheduling.nysfiretools.com` as a Railway custom domain and copy Railway's CNAME and TXT verification records to the existing DNS provider.

## Administration

The global NYSFIRETOOLS header is shared with the main site, including account/sign-in pages. Global destinations stay in the same tab; the Scheduler-specific menu lives directly underneath. Mobile always exposes Home, a global Menu button, and a separate Scheduler menu. Keep `static/css/site-navigation.css` and `static/js/site-navigation.js` identical to the main site's `public/assets` copies. This is a presentation change, not an account/cookie/domain migration.

Global administrators can reach Visits and Popular Tools from Administration. The dashboard lives at the main site's `/admin/usage`. Migration `accounts.0012_usagedaily` adds aggregate daily counts (date, fixed tool code, views); `0013_usagedaily_reach` adds nullable aggregate audience sketches. Successful tool-page GETs count once; auth/admin, errors, redirects, downloads, recognized bots and prefetches are excluded. No raw user IDs, browser IDs, IPs, request URLs, individual events or content are stored. The main server reads the aggregate-only `/accounts/nysfiretools/usage/` endpoint using the existing SSO secret and a purpose-scoped HMAC valid for 60 seconds. Unsigned browser requests are forbidden. No new configuration is needed. History is not backfilled.

Audience counts are estimates from sparse HyperLogLog p=12 registers (4,096 registers, typical standard error ~1.6%). The separate signed `nysfiretools_usage_v1` first-party cookie has a fixed 90-day lifetime, is HttpOnly/SameSite=Lax/Secure in production, and is shared only under `nysfiretools.com`. It is not an authentication token, does not renew a session, and cannot undo logout. No visitor-facing notice is added. DNT/GPC requests count page views only, with no browser/account counter or new usage cookie.

Keep `accounts/usage_reach.py` compatible with the main site's `lib/usage-reach.js`: purpose-separated HMAC, cookie signature, and register encoding have cross-language golden tests. Canonical `scheduler:<id>` account subjects deduplicate signed-in accounts across tools/devices. Only mergeable registers are stored and sent over the signed server bridge, never a full identifier/hash or a list of users. Reach fields are cleared after 90 calendar days during the next eligible page's cleanup; totals are preserved. The main UI displays unique browsers, new browsers, returning browsers (recognized on a later calendar day), active accounts, and per-tool estimates. New/returning can overlap within a period; browser/account counts must not be added. Raw sketches never appear in browser HTML. Existing pre-feature rows retain null reach data.

Django superusers provide statewide/system administration from the central Administration page. Organization administrators receive an `Organization administrator` role at the State, county, academy, or department/agency level. State and academy scopes cover all active organizations, county scopes include agencies in that county, and agency scopes are limited to that agency. Scoped editing is enforced in scheduling views; schedule visibility remains shared.

The bundled department seed is sourced from the official New York State Fire Department Directory (`https://data.ny.gov/resource/qfsu-zcpv`). Replacing the JSON snapshot requires reviewing same-county normalized-name duplicates and FDID inconsistencies before creating a new data migration.
