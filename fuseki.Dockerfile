FROM eclipse-temurin:17-jre-jammy

ARG FUSEKI_VERSION=5.2.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl tini && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL \
      "https://archive.apache.org/dist/jena/binaries/apache-jena-fuseki-${FUSEKI_VERSION}.tar.gz" \
      | tar -xz -C /opt && \
    mv "/opt/apache-jena-fuseki-${FUSEKI_VERSION}" /opt/fuseki

WORKDIR /opt/fuseki

VOLUME /fuseki/databases

EXPOSE 3030

ENTRYPOINT ["/usr/bin/tini", "--", "./fuseki-server"]
CMD ["--update", "--tdb2", "--loc", "/fuseki/databases/tmf921", "/tmf921"]
