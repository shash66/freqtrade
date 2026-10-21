# Minimal Anvil image built from a locally available binary, for hosts that cannot pull ghcr.io/foundry-rs/foundry.
# Usage: docker build -f infra/anvil/Dockerfile --build-arg ANVIL_BIN=path/to/anvil -t fll-anvil:local infra/anvil
FROM debian:bookworm-slim
ARG ANVIL_BIN=anvil
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* \
    && useradd -r -u 10001 anvil
COPY ${ANVIL_BIN} /usr/local/bin/anvil
RUN chmod 0755 /usr/local/bin/anvil
USER anvil
EXPOSE 8545
ENTRYPOINT ["anvil"]
