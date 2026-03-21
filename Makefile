.PHONY: help install test lint format build docker-build docker-push run clean

help:
	@echo "Targets: install test lint format build docker-build docker-push run clean"

install:
	python -m pip install --upgrade pip
	pip install -e .

test:
	pytest -q

lint:
	flake8 src tests || true

format:
	black src tests

build:
	python -m build

IMAGE ?= ghcr.io/OWNER/xyz-platform
TAG ?= latest

docker-build:
	docker build -t $(IMAGE):$(TAG) .

docker-push:
	docker push $(IMAGE):$(TAG)

run:
	python -m xyz_platform

clean:
	rm -rf build dist *.egg-info .pytest_cache __pycache__
