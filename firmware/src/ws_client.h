#pragma once
#include "microlink.h"

// Minimal RFC 6455 client over a MicroLink TCP socket: upgrade handshake,
// masked binary frames out, ping->pong in. Enough for the server's /ws/audio.
microlink_tcp_socket_t *ws_connect(microlink_t *ml, uint32_t ip, uint16_t port,
                                   const char *host, const char *path);
// false => connection is dead (closed, errored, or server sent close); caller should close+reconnect.
bool ws_send_binary(microlink_tcp_socket_t *s, const void *data, size_t len);
