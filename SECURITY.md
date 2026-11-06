# Security policy

token-bar's main promise is that **it never sends anything out**: it reads files your coding tools already keep on your machine, and writes only its own small settings files. We treat anything that breaks that promise as a security problem.

## What to report

- token-bar makes a network call, or sends your data anywhere.
- token-bar reads or stores something it shouldn't, such as sign-in tokens, passwords, or the Keychain.
- token-bar writes or changes files outside its own settings, or changes a setting you didn't agree to.
- A way for a crafted log file or settings file to run code or cause harm.

## How to report

Please report privately, so a fix can go out before details are public. Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**.

Include what you did, what you expected, and what happened, with anything private removed. We'll acknowledge your report as soon as we can and keep you updated while it's fixed.

## Which versions get fixes

The latest release. Update with `/plugin marketplace update token-bar` and `/plugin update token-bar@token-bar`.

## Not a security issue

Wrong or out-of-date usage numbers are bugs, not vulnerabilities. Please [open an issue](https://github.com/56steve/Token-Bar/issues/new/choose) for those.
