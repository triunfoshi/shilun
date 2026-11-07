# HTTPS certificates

Place the certificate chain at `fullchain.pem` and the private key at
`privkey.key`, then start the `edge` Compose profile. Certificate and key files
are ignored by Git.

Do not use the self-signed development certificate for public access. The
recommended production path is an Alibaba Cloud certificate bound to the
chosen domain, or HTTPS termination at an Alibaba Cloud load balancer.
