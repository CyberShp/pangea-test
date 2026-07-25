#ifndef MINI_STORAGE_H
#define MINI_STORAGE_H

#include <stdbool.h>
#include <stddef.h>

enum conn_state {
    CONN_NEW = 0,
    CONN_CONNECTING,
    CONN_LIVE,
    CONN_RECOVERING,
    CONN_CLOSED
};

struct connection {
    enum conn_state state;
    unsigned int retry_count;
    unsigned int inflight;
    char *buffer;
    size_t buffer_size;
};

int connection_open(struct connection *conn, size_t buffer_size);
void connection_close(struct connection *conn);
int connection_handle_event(struct connection *conn, int event);
int connection_recover(struct connection *conn, bool transport_ready);
int allocate_request_buffer(struct connection *conn, size_t size, bool inject_failure);
void release_request_buffer(struct connection *conn);
int submit_request(struct connection *conn, bool transport_error);

#endif
