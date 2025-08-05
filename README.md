# Charmed Kafka UI Operator

[![Release](https://github.com/canonical/kafka-ui-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/kafka-ui-operator/actions/workflows/release.yaml)
[![Tests](https://github.com/canonical/kafka-ui-operator/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/canonical/kafka-ui-operator/actions/workflows/ci.yaml?query=branch%3Amain)

The Charmed Kafka UI Operator delivers automated operations management from day 0 to day 2 on [Kafkbat's Kafka UI](https://github.com/kafbat/kafka-ui), and enables users to:

- View Apache Kafka cluster configuration, topics, ACLs, consumer groups and more
- Broker performance monitoring via JMX metrics dashboards
- Authentication and SSL encryption enabled by default
- Integrations with other Charmed Apache Kafka operators, like [Charmed Apache Kafka Connect](https://charmhub.io/kafka-connect) and [Charmed Karapace](https://charmhub.io/karapace)

The Charmed Kafka UI Operator uses the latest [`charmed-kafka-ui` snap](https://github.com/canonical/charmed-kafka-ui-snap) containing Kafbat's Kafka UI, distributed by Canonical.

## Usage

Before using Charmed Kafka UI, an Apache Kafka cluster needs to be deployed. The Charmed Apache Kafka operator can be deployed as follows:

```bash
juju deploy kafka --channel 4/edge -n 3 --config roles="broker,controller"
```

To deploy the Charmed Kafka UI operator and relate it with the Apache Kafka cluster, use the following commands:

```bash
juju deploy kafka-ui --channel latest/edge
juju integrate kafka-ui kafka
```

Monitor the deployment via the `juju status` command. Once all the units show as `active|idle`, the Kafka UI is ready to be used.

In order to access the Kafka UI, first save the internal CA certificate from the application to your local system:

```bash
juju ssh kafka-ui/0 sudo -i 'cat /var/snap/charmed-kafka-ui/current/etc/kafka-ui/ca.pem' 2>/dev/null > /tmp/ca.pem
```

Then, follow your browser's instructions for setting up Certificate Authorities (CAs), using the above CA. For example:
- **Firefox** - [Set up Certificate Authorities (CAs) in Firefox](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox)
- **Google Chrome** - [Set up TLS (or SSL) inspection on Chrome devices](https://support.google.com/chrome/a/answer/3505249?hl=en)

Once SSL has been correctly configured in your browser and the Charmed Kafka UI CA has been added, get the password for the `admin` user with the following command:

```bash
juju ssh kafka-ui/0 sudo -i 'cat /var/snap/charmed-kafka-ui/current/etc/kafka-ui/application-local.yml' 2>/dev/null | \
    yq '.spring.security.user.password' | \
    sed 's/\"//g'
```

Finally, you can now reach the Kafka UI in your browser on port `8080` with the `admin` username and corresponding password derived above. For example, if using Firefox:

```bash
IP=juju show-unit kafka-ui/0 --format json | jq '."kafka-ui/0"."public-address"'

firefox --new-tab https://$IP:8080
```

## Relations

The Charmed Kafka UI Operator supports Juju [relations](https://documentation.ubuntu.com/juju/latest/reference/relation/) for interfaces listed below.

- A `kafka_client` (**required**) with Charmed Apache Kafka
- A `karapace_client` integration with Charmed Karapace
- A `connect_client` integration with Charmed Apache Kafka Connect
- A `tls-certificates` interface with any provider charm to manager certificates

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](https://github.com/canonical/kafka-connect-operator/blob/main/CONTRIBUTING.md) for developer guidance.

### We are Hiring!

Also, if you truly enjoy working on open-source projects like this one and you would like to be part of the OSS revolution, please don't forget to check out the [open positions](https://canonical.com/careers/all) we have at [Canonical](https://canonical.com/). 

## License

The Charmed Kafka Connect Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](https://github.com/canonical/kafka-connect-operator/blob/main/LICENSE) for more information.
