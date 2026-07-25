#include "storage.h"
#include <stdlib.h>

int allocate_request_buffer(struct connection *conn, size_t size, bool inject_failure)
{
    if (conn == NULL || size == 0) {
        return -22;
    }
    conn->buffer = malloc(size);
    if (conn->buffer == NULL) {
        return -12;
    }
    conn->buffer_size = size;
    if (inject_failure) {
        /* Deliberate fixture defect: allocated buffer is not released. */
        return -5; /* EIO */
    }
    return 0;
}

void release_request_buffer(struct connection *conn)
{
    if (conn == NULL) {
        return;
    }
    free(conn->buffer);
    conn->buffer = NULL;
    conn->buffer_size = 0;
}
