#!/usr/bin/env python3
"""Tests for NetworkModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.network_model import NetworkModel


def _minimal_network() -> dict:
    return {
        "meta": {"name": "core-network"},
        "spec": {
            "networks": [
                {
                    "name": "vpc-main",
                    "address_space": ["10.0.0.0/16"],
                    "subnets": [{"name": "web", "cidr": "10.0.1.0/24"}],
                    "default_tags": {"environment": "test"},
                }
            ]
        },
    }


def test_network_minimal_is_valid():
    """A minimal network document (only required fields) validates successfully."""
    model = NetworkModel.model_validate(_minimal_network())
    assert model.meta.name == "core-network"
    assert model.spec.networks[0].name == "vpc-main"
    assert model.spec.networks[0].subnets[0].cidr == "10.0.1.0/24"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "network"


def test_network_rejects_invalid_literal_cidr():
    """A malformed literal CIDR is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "not-a-cidr"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_accepts_value_tokens_for_cidr():
    """A subnet cidr may embed '${var:}' tokens instead of a literal."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${var:web_subnet_cidr}"
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].subnets[0].cidr == "${var:web_subnet_cidr}"


def test_network_rejects_malformed_value_token():
    """An unknown token kind in a CIDR field is rejected at Phase 1."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${vars:web_subnet_cidr}"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_overlapping_subnets():
    """Overlapping literal subnet CIDRs within a network are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"].append({"name": "app", "cidr": "10.0.1.128/25"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_skips_overlap_check_when_cidr_is_a_token():
    """Overlap checking is skipped (not falsely triggered) when a CIDR is a Value binding."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "${var:web_subnet_cidr}"
    data["spec"]["networks"][0]["subnets"].append({"name": "app", "cidr": "10.0.1.0/24"})
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].subnets[1].cidr == "10.0.1.0/24"


def test_network_rejects_subnet_outside_address_space():
    """A subnet CIDR that doesn't fit any address_space entry is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"][0]["cidr"] = "192.168.1.0/24"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_self_peering():
    """A peering that targets the network itself is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "loopback", "target": "vpc-main"}]
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_peering_to_unknown_network():
    """A peering targeting a network not defined in spec.networks is rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_accepts_valid_peering():
    """A peering targeting another defined network is accepted."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    data["spec"]["networks"].append(
        {
            "name": "vpc-other",
            "address_space": ["10.1.0.0/16"],
            "subnets": [{"name": "web", "cidr": "10.1.1.0/24"}],
            "default_tags": {"environment": "test"},
        }
    )
    model = NetworkModel.model_validate(data)
    assert model.spec.networks[0].peerings[0].target == "vpc-other"


def test_network_rejects_overlapping_peered_networks():
    """Peered networks with overlapping literal address spaces are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    data["spec"]["networks"].append(
        {
            "name": "vpc-other",
            "address_space": ["10.0.0.0/16"],
            "subnets": [{"name": "web", "cidr": "10.0.2.0/24"}],
            "default_tags": {"environment": "test"},
        }
    )
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_duplicate_network_names():
    """Duplicate network names within spec.networks are rejected."""
    data = _minimal_network()
    data["spec"]["networks"].append(dict(data["spec"]["networks"][0]))
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_duplicate_subnet_names():
    """Duplicate subnet names within a network are rejected."""
    data = _minimal_network()
    data["spec"]["networks"][0]["subnets"].append({"name": "web", "cidr": "10.0.5.0/24"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_network()
    data["spec"]["references"] = {"variables": ["web_subnet_cidr"]}
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_network_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_network()
    data["spec"]["networks"][0]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


# ---------------------------------------------------------------------------
# prefix_length subnets and ipam (docs/design/subnet-addressing.md, Phase 1)
# ---------------------------------------------------------------------------


def _network(**fields) -> dict:
    data = _minimal_network()
    data["spec"]["networks"][0].update(fields)
    return data


def test_subnet_accepts_a_prefix_length_instead_of_a_cidr():
    data = _network(subnets=[{"name": "aks-nodes", "prefix_length": 25}])
    subnet = NetworkModel.model_validate(data).spec.networks[0].subnets[0]
    assert (subnet.cidr, subnet.prefix_length) == (None, 25)


@pytest.mark.parametrize(
    "subnet",
    [
        {"name": "both", "cidr": "10.0.1.0/24", "prefix_length": 24},
        {"name": "neither"},
    ],
    ids=["both", "neither"],
)
def test_subnet_needs_exactly_one_of_cidr_and_prefix_length(subnet):
    with pytest.raises(ValidationError, match="exactly one of 'cidr' or 'prefix_length'"):
        NetworkModel.model_validate(_network(subnets=[subnet]))


@pytest.mark.parametrize("length", [0, 129])
def test_subnet_prefix_length_is_bounded(length):
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(_network(subnets=[{"name": "web", "prefix_length": length}]))


def test_prefix_length_subnets_take_no_part_in_the_overlap_check():
    """A sized subnet has no address yet, so it can't overlap a fixed one."""
    data = _network(subnets=[{"name": "web", "cidr": "10.0.1.0/24"}, {"name": "app", "prefix_length": 24}])
    assert len(NetworkModel.model_validate(data).spec.networks[0].subnets) == 2


def test_fixed_subnets_still_overlap_check_beside_a_prefix_length_subnet():
    data = _network(
        subnets=[
            {"name": "web", "cidr": "10.0.1.0/24"},
            {"name": "app", "cidr": "10.0.1.128/25"},
            {"name": "aks", "prefix_length": 25},
        ]
    )
    with pytest.raises(ValidationError, match="overlap"):
        NetworkModel.model_validate(data)


def test_fixed_subnet_outside_the_address_space_is_still_rejected_beside_a_prefix_length_subnet():
    data = _network(subnets=[{"name": "web", "cidr": "192.168.1.0/24"}, {"name": "aks", "prefix_length": 25}])
    with pytest.raises(ValidationError, match="does not fit"):
        NetworkModel.model_validate(data)


@pytest.mark.parametrize(
    ("address_space", "length"),
    [(["10.0.0.0/16"], 15), (["10.0.0.0/16"], 64), (["10.0.0.0/24", "10.1.0.0/24"], 23)],
    ids=["larger-than-the-range", "longer-than-ipv4", "larger-than-every-range"],
)
def test_prefix_length_that_cannot_fit_any_range_is_rejected(address_space, length):
    data = _network(address_space=address_space, subnets=[{"name": "aks", "prefix_length": length}])
    with pytest.raises(ValidationError, match=r"'aks' \(/\d+\) .* does not fit"):
        NetworkModel.model_validate(data)


@pytest.mark.parametrize(
    ("address_space", "length"),
    [(["10.0.0.0/16"], 16), (["10.0.0.0/16"], 32), (["10.0.0.0/24", "fd00::/48"], 64)],
    ids=["the-whole-range", "a-single-address", "fits-the-ipv6-range"],
)
def test_prefix_length_that_fits_a_range_is_accepted(address_space, length):
    data = _network(address_space=address_space, subnets=[{"name": "aks", "prefix_length": length}])
    assert NetworkModel.model_validate(data).spec.networks[0].subnets[0].prefix_length == length


def test_prefix_length_fit_is_skipped_when_the_address_space_is_a_token():
    data = _network(address_space=["${var:vnet_space}"], subnets=[{"name": "aks", "prefix_length": 8}])
    assert NetworkModel.model_validate(data).spec.networks[0].subnets[0].prefix_length == 8


def test_network_may_have_no_subnets():
    """A network whose subnets are all declared by the workloads that use it."""
    data = _network()
    del data["spec"]["networks"][0]["subnets"]
    assert NetworkModel.model_validate(data).spec.networks[0].subnets == []


def test_network_with_ipam_needs_no_address_space():
    data = _network(ipam={"type": "azure-avnm", "pool": "${var:ipam_pool_id}", "prefix_length": 22})
    del data["spec"]["networks"][0]["address_space"]
    data["spec"]["networks"][0]["subnets"] = [{"name": "app1", "prefix_length": 27}]
    network = NetworkModel.model_validate(data).spec.networks[0]
    assert network.address_space is None
    assert network.ipam is not None
    assert (network.ipam.type, network.ipam.pool, network.ipam.prefix_length) == (
        "azure-avnm",
        "${var:ipam_pool_id}",
        22,
    )


def test_network_with_ipam_and_address_space_keeps_the_literal_checks():
    """With both, the ranges are what the IPAM manages, so strata still checks against them."""
    data = _network(ipam={"type": "netbox", "configuration": {"prefix_id": 42}})
    data["spec"]["networks"][0]["subnets"] = [{"name": "web", "cidr": "192.168.1.0/24"}]
    with pytest.raises(ValidationError, match="does not fit"):
        NetworkModel.model_validate(data)


def test_network_needs_an_address_space_or_an_ipam():
    data = _network()
    del data["spec"]["networks"][0]["address_space"]
    with pytest.raises(ValidationError, match="needs 'address_space', 'ipam', or both"):
        NetworkModel.model_validate(data)


def test_ipam_needs_a_type():
    data = _network(ipam={"pool": "pool-1"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_ipam_pool_rejects_a_malformed_value_token():
    data = _network(ipam={"type": "azure-avnm", "pool": "${vars:pool}"})
    with pytest.raises(ValidationError):
        NetworkModel.model_validate(data)


def test_subnets_needing_more_addresses_than_the_address_space_are_rejected():
    """Two /25s and a /24 need 512 addresses; a /24 holds 256."""
    data = _network(
        address_space=["10.0.0.0/24"],
        subnets=[
            {"name": "web", "cidr": "10.0.0.0/25"},
            {"name": "app", "prefix_length": 25},
            {"name": "data", "prefix_length": 24},
        ],
    )
    with pytest.raises(ValidationError, match="need 512 addresses, .* holds only 256"):
        NetworkModel.model_validate(data)


def test_subnets_that_exactly_fill_the_address_space_are_accepted():
    data = _network(
        address_space=["10.0.0.0/24"],
        subnets=[{"name": "web", "cidr": "10.0.0.0/25"}, {"name": "app", "prefix_length": 25}],
    )
    assert len(NetworkModel.model_validate(data).spec.networks[0].subnets) == 2


def test_capacity_counts_several_ranges_and_overlapping_ranges_once():
    """10.0.0.0/24 + 10.0.1.0/24 hold 512; the overlapping 10.0.0.0/25 adds nothing."""
    ranges = ["10.0.0.0/24", "10.0.1.0/24", "10.0.0.0/25"]
    fits = _network(
        address_space=ranges, subnets=[{"name": "a", "prefix_length": 24}, {"name": "b", "prefix_length": 24}]
    )
    assert NetworkModel.model_validate(fits)
    too_many = _network(
        address_space=ranges,
        subnets=[
            {"name": "a", "prefix_length": 24},
            {"name": "b", "prefix_length": 24},
            {"name": "c", "prefix_length": 30},
        ],
    )
    with pytest.raises(ValidationError, match="holds only 512"):
        NetworkModel.model_validate(too_many)


@pytest.mark.parametrize(
    ("address_space", "subnets"),
    [
        (["${var:vnet_space}"], [{"name": "a", "prefix_length": 24}, {"name": "b", "prefix_length": 24}]),
        (["10.0.0.0/24"], [{"name": "a", "cidr": "${var:a_cidr}"}, {"name": "b", "prefix_length": 24}]),
        (["10.0.0.0/24", "fd00::/120"], [{"name": "a", "prefix_length": 24}, {"name": "b", "prefix_length": 120}]),
    ],
    ids=["token-range", "token-fixed-subnet", "mixed-ip-versions"],
)
def test_capacity_is_not_checked_when_it_cannot_be_counted(address_space, subnets):
    assert NetworkModel.model_validate(_network(address_space=address_space, subnets=subnets))


def _ipam_network(subnets: list[dict], address_space: list[str] | None = None, prefix_length: int | None = 22) -> dict:
    data = _minimal_network()
    network: dict = {"name": "spoke", "ipam": {"type": "azure-avnm", "prefix_length": prefix_length}}
    if address_space is not None:
        network["address_space"] = address_space
    network["subnets"] = subnets
    data["spec"]["networks"] = [network]
    return data


def test_ipam_block_rejects_a_subnet_larger_than_it():
    """No literal address space: the /22 the network asks the IPAM for is the space a /21 cannot fit."""
    data = _ipam_network([{"name": "big", "prefix_length": 21}])
    with pytest.raises(ValidationError, match=r"\(/21\) in network 'spoke' is larger than the /22 block"):
        NetworkModel.model_validate(data)


def test_ipam_block_rejects_subnets_that_together_need_more_than_it_holds():
    """Three /23s need 1536 addresses; a /22 holds 1024."""
    data = _ipam_network([{"name": n, "prefix_length": 23} for n in ("a", "b", "c")])
    with pytest.raises(
        ValidationError, match="need 1536 addresses, but the /22 block it asks the IPAM for holds only 1024"
    ):
        NetworkModel.model_validate(data)


def test_ipam_block_counts_a_literal_fixed_subnet():
    """A fixed /23 and two /24s fill the /22 exactly; one more /28 does not fit."""
    fits = [
        {"name": "x", "cidr": "10.0.0.0/23"},
        {"name": "a", "prefix_length": 24},
        {"name": "b", "prefix_length": 24},
    ]
    assert NetworkModel.model_validate(_ipam_network(fits))
    with pytest.raises(ValidationError, match="need 1040 addresses"):
        NetworkModel.model_validate(_ipam_network([*fits, {"name": "c", "prefix_length": 28}]))


def test_ipam_block_of_an_ipv6_network_is_counted_as_ipv6():
    """A /56 holds 256 /64s; 257 do not fit (lengths past /32 mean IPv6)."""
    fits = [{"name": f"s{i}", "prefix_length": 64} for i in range(256)]
    assert NetworkModel.model_validate(_ipam_network(fits, prefix_length=56))
    with pytest.raises(ValidationError, match="the /56 block"):
        NetworkModel.model_validate(_ipam_network([*fits, {"name": "extra", "prefix_length": 64}], prefix_length=56))


@pytest.mark.parametrize(
    ("address_space", "prefix_length"),
    [(["10.0.0.0/16"], 22), (None, None)],
    ids=["literal-address-space-wins", "no-ipam-prefix-length"],
)
def test_ipam_block_is_not_the_space_when_it_should_not_be(address_space, prefix_length):
    """A literal address space is what the checks use (a /21 fits the /16); without a prefix_length there is
    nothing to check."""
    data = _ipam_network(
        [{"name": "big", "prefix_length": 21}], address_space=address_space, prefix_length=prefix_length
    )
    assert NetworkModel.model_validate(data)


def test_capacity_still_counts_sized_subnets_beside_a_token_fixed_subnet():
    """The token-valued fixed subnet is left out (a lower bound); the sized ones alone need 384 of 256."""
    data = _network(
        address_space=["10.0.0.0/24"],
        subnets=[
            {"name": "x", "cidr": "${var:x_cidr}"},
            {"name": "a", "prefix_length": 24},
            {"name": "b", "prefix_length": 25},
        ],
    )
    with pytest.raises(ValidationError, match="need 384 addresses, .* holds only 256"):
        NetworkModel.model_validate(data)


def test_peering_overlap_check_skips_a_network_without_address_space():
    data = _minimal_network()
    data["spec"]["networks"][0]["peerings"] = [{"name": "to-other", "target": "vpc-other"}]
    data["spec"]["networks"].append({"name": "vpc-other", "ipam": {"type": "azure-avnm"}})
    assert NetworkModel.model_validate(data).spec.networks[1].address_space is None
