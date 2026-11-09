"use strict";

class TervicSdkError extends Error {
    constructor(message, code, cause) {
        super(message);
        this.name = "TervicSdkError";
        this.code = code;

        if (cause !== undefined && cause !== null && cause.statusCode !== undefined) {
            this.statusCode = cause.statusCode;
        }

        if (cause !== undefined) {
            this.cause = cause;
        }
    }
}

module.exports = { TervicSdkError };
