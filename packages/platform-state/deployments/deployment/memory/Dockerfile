ARG POSTGRES_BASE=postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15
FROM ${POSTGRES_BASE} AS build
COPY pgvector.tar.gz /build/pgvector.tar.gz
RUN apk add --no-cache build-base clang21 llvm21-dev \
    && mkdir /build/pgvector \
    && tar -xzf /build/pgvector.tar.gz -C /build/pgvector --strip-components=1 \
    && cd /build/pgvector \
    && make OPTFLAGS="" \
    && make install DESTDIR=/build/install \
    && mkdir -p /build/install/usr/share/doc/pgvector \
    && cp LICENSE README.md /build/install/usr/share/doc/pgvector/

FROM ${POSTGRES_BASE}
COPY --from=build /build/install/ /
LABEL org.opencontainers.image.title="HostCapabilities native-memory PostgreSQL canary" \
      org.opencontainers.image.source="https://github.com/pgvector/pgvector" \
      org.opencontainers.image.revision="f37c13f68b57d2c3472b2214fbcff699d6d34876" \
      org.opencontainers.image.licenses="PostgreSQL"
