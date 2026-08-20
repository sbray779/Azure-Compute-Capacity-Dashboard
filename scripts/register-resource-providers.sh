#!/usr/bin/env sh
set -eu

az provider register --namespace Microsoft.DurableTask --wait
