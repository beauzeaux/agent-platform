'use strict';
const client = require('./chart/files/client');
if (require.main === module) client.startAdapter();
module.exports = client;
