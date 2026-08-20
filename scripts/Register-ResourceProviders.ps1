$ErrorActionPreference = 'Stop'

az provider register --namespace Microsoft.DurableTask --wait

if ($LASTEXITCODE -ne 0) {
    throw 'Failed to register the Microsoft.DurableTask resource provider.'
}
