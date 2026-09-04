# Security policy

Vox Relay is published by InsyncTech.

## Reporting a vulnerability

Email support@insynctech.io. Put "Vox Relay security" in the subject line. Do not open a public
issue for a security problem.

Include what you can:

- the version (see `VERSION.txt` or `VERSION.json` in the repo, or the app's About panel)
- steps to reproduce
- what you observed and what you expected
- any log excerpt, with personal data removed

We read every report and reply from the same address. We do not use bug-bounty platforms and we
do not pay bounties.

## Supported versions

The latest tagged release is supported. Older tags receive no fixes.

## Verify what you downloaded

Every release publishes `SHA256SUMS`, `RELEASE.sha256`, `RELEASE.sha256.sig`, and
`allowed_signers`. The two commands that check them are in the README under "Verify your
download". If either command fails, do not open the download; email support@insynctech.io with
the output.
