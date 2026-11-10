# Identity and authorization

This package owns identity policy rather than token material:

- actor UID/subject/profile enrollment and revocation;
- exact profile and resource authorization;
- Agency policy/plugin preparation;
- Windows/WSL relay source;
- access and management deployment surfaces.

Protected actor registries, bridge credentials, WAM tokens, runtime plugin archives, certificates, and generated public
projections are excluded. The host validates signed actor identity and current enrollment on every request.
