FROM eclipse-temurin:21-jre-jammy

ARG FUSEKI_VERSION=6.1.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl tini && \
    rm -rf /var/lib/apt/lists/* && \
    ( curl -fsSL "https://dlcdn.apache.org/jena/binaries/apache-jena-fuseki-${FUSEKI_VERSION}.tar.gz" \
      || curl -fsSL "https://archive.apache.org/dist/jena/binaries/apache-jena-fuseki-${FUSEKI_VERSION}.tar.gz" ) \
    | tar -xz -C /opt && \
    mv "/opt/apache-jena-fuseki-${FUSEKI_VERSION}" /opt/fuseki

WORKDIR /opt/fuseki

# Assembler config and TIO inference rules
RUN mkdir -p /opt/fuseki/rules
COPY fuseki-config.ttl /opt/fuseki/run/config.ttl
COPY ontology/jena-rules/tio_all.rules /opt/fuseki/rules/tio_all.rules

VOLUME /fuseki/databases

EXPOSE 3030

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/sh", "-c", "mkdir -p /fuseki/databases/tmf921 && exec ./fuseki-server --config /opt/fuseki/run/config.ttl"]
