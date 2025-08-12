az login
ACR_NAME="anildwapremacr"
RESOURCE_GROUP="aifoundry-hubs"
az acr build --registry $ACR_NAME -g $RESOURCE_GROUP --image anildwamcpserver:1.0 --file aca_deployment/Dockerfile .