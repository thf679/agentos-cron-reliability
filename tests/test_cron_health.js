#!/usr/bin/env node
// test_cron_health.js — D3: Verify /api/cron/health endpoint JSON structure
"use strict";

const http = require("http");

const HOST = "127.0.0.1";
const PORT = 51763;
const PATH = "/api/cron/health";

console.log("=== D3: Cron Health Test ===");
console.log(`GET http://${HOST}:${PORT}${PATH}`);

function fetchJSON(host, port, path) {
    return new Promise((resolve, reject) => {
        const req = http.get({ host, port, path, timeout: 10000 }, (res) => {
            let data = "";
            res.on("data", (chunk) => { data += chunk; });
            res.on("end", () => {
                try {
                    resolve(JSON.parse(data));
                } catch (e) {
                    reject(new Error(`Invalid JSON: ${e.message}\nRaw: ${data.slice(0, 200)}`));
                }
            });
        });
        req.on("error", (e) => reject(new Error(`HTTP error: ${e.message}`)));
        req.on("timeout", () => { req.destroy(); reject(new Error("Request timed out")); });
    });
}

async function main() {
    let fail = 0;

    try {
        const json = await fetchJSON(HOST, PORT, PATH);
        console.log("Response:", JSON.stringify(json, null, 2));

        // Required keys
        const requiredKeys = ["total_jobs", "healthy", "failed", "paused", "failures"];
        for (const key of requiredKeys) {
            if (!(key in json)) {
                console.log(`FAIL: Missing key '${key}' in response`);
                fail++;
            } else {
                console.log(`PASS: Key '${key}' present = ${JSON.stringify(json[key])}`);
            }
        }

        // Type checks
        if (typeof json.total_jobs !== "number") {
            console.log(`FAIL: total_jobs should be number, got ${typeof json.total_jobs}`);
            fail++;
        } else {
            console.log(`PASS: total_jobs is number = ${json.total_jobs}`);
        }

        if (typeof json.healthy !== "number") {
            console.log(`FAIL: healthy should be number, got ${typeof json.healthy}`);
            fail++;
        }

        if (typeof json.failed !== "number") {
            console.log(`FAIL: failed should be number, got ${typeof json.failed}`);
            fail++;
        }

        // Assert failed <= total_jobs
        if (typeof json.failed === "number" && typeof json.total_jobs === "number") {
            if (json.failed <= json.total_jobs) {
                console.log(`PASS: failed (${json.failed}) <= total_jobs (${json.total_jobs})`);
            } else {
                console.log(`FAIL: failed (${json.failed}) > total_jobs (${json.total_jobs})`);
                fail++;
            }
        }

        // failures should be an array
        if (!Array.isArray(json.failures)) {
            console.log(`FAIL: 'failures' should be array, got ${typeof json.failures}`);
            fail++;
        } else {
            console.log(`PASS: 'failures' is array with ${json.failures.length} entries`);
            // Check each failure has expected fields
            for (let i = 0; i < json.failures.length; i++) {
                const f = json.failures[i];
                if (!f.job_id || !f.name || !f.last_status) {
                    console.log(`FAIL: Failure entry ${i} missing required fields: ${JSON.stringify(f)}`);
                    fail++;
                }
            }
        }

        // Assert healthy + failed + paused <= total_jobs (could be == but paused counts as not-healthy-not-failed)
        if (typeof json.healthy === "number" && typeof json.failed === "number" && typeof json.paused === "number" && typeof json.total_jobs === "number") {
            if (json.healthy + json.failed + json.paused <= json.total_jobs) {
                console.log(`PASS: healthy+failed+paused (${json.healthy + json.failed + json.paused}) <= total_jobs (${json.total_jobs})`);
            } else {
                console.log(`FAIL: healthy+failed+paused (${json.healthy + json.failed + json.paused}) > total_jobs (${json.total_jobs})`);
                fail++;
            }
        }

    } catch (e) {
        console.log(`FAIL: ${e.message}`);
        fail++;
    }

    if (fail > 0) {
        console.log(`TEST_RESULT: FAIL (${fail} assertion(s) failed)`);
        process.exit(1);
    } else {
        console.log("TEST_RESULT: PASS");
        process.exit(0);
    }
}

main();
