#include "storage.h"
#include <stdio.h>
#include <stdlib.h>

int connection_open(struct connection *conn, size_t buffer_size)
{
    if (conn == NULL || buffer_size == 0) {
        return -22; /* EINVAL */
    }
    conn->state = CONN_CONNECTING;
    conn->retry_count = 0;
    conn->inflight = 0;
    conn->buffer = malloc(buffer_size);
    if (conn->buffer == NULL) {
        fprintf(stderr, "CONN_ALLOC_FAILED size=%zu\n", buffer_size);
        conn->state = CONN_CLOSED;
        return -12; /* ENOMEM */
    }
    conn->buffer_size = buffer_size;
    conn->state = CONN_LIVE;
    printf("CONN_LIVE size=%zu\n", buffer_size);
    return 0;
}

void connection_close(struct connection *conn)
{
    if (conn == NULL) {
        return;
    }
    free(conn->buffer);
    conn->buffer = NULL;
    conn->buffer_size = 0;
    conn->state = CONN_CLOSED;
    printf("CONN_CLOSED\n");
}
