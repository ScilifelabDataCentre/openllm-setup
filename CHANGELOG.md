# Changelog

Notable changes to this repository, newest first. One line per change.

## Unreleased

### Added

- Open WebUI: ALTCHA Browser Gate Event serving a browser check for the NGINX
  proxy; clearance persists across tabs and cross-site links.
- Open WebUI: Registration Questionnaire Event asking pending users about
  affiliation and planned use; answers are stored in whale-auth-tools.
- Repo: `CHANGELOG.md` and a `sync-changelog` agent skill that keeps it updated.

### Changed

- Open WebUI: Account Approval Notification returns users approved without a
  questionnaire submission to pending (`require_questionnaire`, default on).
- Open WebUI: Condensed the Functions README to setup, settings, and key caveats.
