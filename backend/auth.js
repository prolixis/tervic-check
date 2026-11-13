"use strict";

const {
    createHash,
    randomBytes,
    scrypt: scryptCallback,
    timingSafeEqual
} = require("node:crypto");
const { promisify } = require("node:util");

const scrypt = promisify(scryptCallback);
const KEY_LENGTH = 64;
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const DUMMY_HASH = `scrypt$${Buffer.alloc(16).toString("base64url")}$${Buffer.alloc(KEY_LENGTH).toString("base64url")}`;

async function hashPassword(password) {
    const salt = randomBytes(16);
    const derivedKey = await scrypt(password, salt, KEY_LENGTH, { maxmem: 64 * 1024 * 1024 });
    return `scrypt$${salt.toString("base64url")}$${derivedKey.toString("base64url")}`;
}

async function verifyPassword(password, encodedHash) {
    const [algorithm, encodedSalt, encodedKey] = encodedHash.split("$");
    if (algorithm !== "scrypt" || !encodedSalt || !encodedKey) return false;
    const salt = Buffer.from(encodedSalt, "base64url");
    const expected = Buffer.from(encodedKey, "base64url");
    if (salt.length !== 16 || expected.length !== KEY_LENGTH) return false;
    const actual = await scrypt(password, salt, expected.length, { maxmem: 64 * 1024 * 1024 });
    return timingSafeEqual(actual, expected);
}

async function verifyLoginPassword(password, encodedHash) {
    const matches = await verifyPassword(password, encodedHash || DUMMY_HASH);
    return Boolean(encodedHash) && matches;
}

function createSessionToken() {
    const token = randomBytes(32).toString("base64url");
    return {
        token,
        tokenHash: createHash("sha256").update(token).digest("hex"),
        expiresAt: new Date(Date.now() + SESSION_TTL_MS)
    };
}

function hashSessionToken(token) {
    return createHash("sha256").update(token).digest("hex");
}

module.exports = {
    createSessionToken,
    hashPassword,
    hashSessionToken,
    verifyLoginPassword,
    verifyPassword
};
