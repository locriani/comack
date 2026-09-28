#include "ws_client.h"
#include <stdio.h>
#include <string.h>

// Client frames must be masked; an all-zero key is valid and leaves the payload as-is.
static bool send_frame(microlink_tcp_socket_t *s, uint8_t opcode, const void *data, size_t len) {
    uint8_t hdr[8] = { (uint8_t)(0x80 | opcode) };
    size_t h = 2;
    if (len < 126) {
        hdr[1] = 0x80 | (uint8_t)len;
    } else {
        hdr[1] = 0x80 | 126;
        hdr[2] = len >> 8;
        hdr[3] = len & 0xFF;
        h = 4;
    }
    h += 4; // zero mask key (hdr is zero-initialised)
    return microlink_tcp_send(s, hdr, h) == ESP_OK &&
           (len == 0 || microlink_tcp_send(s, data, len) == ESP_OK);
}

microlink_tcp_socket_t *ws_connect(microlink_t *ml, uint32_t ip, uint16_t port,
                                   const char *host, const char *path) {
    microlink_tcp_socket_t *s = microlink_tcp_connect(ml, ip, port, 5000);
    if (!s) return NULL;

    char req[256];
    int n = snprintf(req, sizeof(req),
        "GET %s HTTP/1.1\r\nHost: %s:%u\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n",
        path, host, port);
    if (microlink_tcp_send(s, req, n) != ESP_OK) { microlink_tcp_close(s); return NULL; }

    char resp[256];
    int r = microlink_tcp_recv(s, resp, sizeof(resp) - 1, 5000);
    if (r <= 0) { microlink_tcp_close(s); return NULL; }
    resp[r] = 0;
    if (!strstr(resp, " 101 ")) { microlink_tcp_close(s); return NULL; }
    return s;
}

bool ws_send_binary(microlink_tcp_socket_t *s, const void *data, size_t len) {
    if (!send_frame(s, 0x2, data, len)) return false;

    // Server pings (uvicorn: every 20s) must be answered or it drops us.
    // ponytail: assumes one small control frame per recv; fine for ping/close only.
    uint8_t in[128];
    int r = microlink_tcp_recv(s, in, sizeof(in), 1);
    if (r < 0) return false;
    if (r >= 2) {
        uint8_t op = in[0] & 0x0F, plen = in[1] & 0x7F;
        if (op == 0x8) return false;
        if (op == 0x9 && plen <= r - 2) return send_frame(s, 0xA, in + 2, plen);
    }
    return true;
}
