'use strict';
const gateway = require('./chart/files/gateway');
if (require.main === module) gateway.startGateway();
module.exports = gateway;
