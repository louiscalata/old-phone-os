<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# VoLTE

Voice on a carrier that requires LTE calling depends on IMS. Two checks are still open:

- whether the package `ofono-binder-plugin-ext-qti` can be installed on this image
- whether oFono's IMS interface registers after that

SMS that travels over IMS has the same dependency. Neither check has been run. **NOT_RUN.**

Until those checks pass, the cellular line can say that calls need VoLTE and that it is not available on this build. No call has been placed.
