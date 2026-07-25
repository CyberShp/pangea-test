#include "storage.h"
#include <stdio.h>

#define EVENT_TRANSPORT_DOWN 1
#define EVENT_TRANSPORT_UP   2
#define EVENT_CLOSE          3

int connection_handle_event(struct connection *conn, int event)
{
    if (conn == NULL) {
        return -22;
    }
    switch (event) {
    case EVENT_TRANSPORT_DOWN:
        if (conn->state != CONN_LIVE) {
            return -16; /* EBUSY */
        }
        conn->state = CONN_RECOVERING;
        fprintf(stderr, "CONN_RECOVERING inflight=%u\n", conn->inflight);
        return 0;
    case EVENT_TRANSPORT_UP:
        if (conn->state != CONN_RECOVERING) {
            return -71; /* EPROTO */
        }
        conn->state = CONN_LIVE;
        printf("CONN_RECOVERED retries=%u\n", conn->retry_count);
        return 0;
    case EVENT_CLOSE:
        connection_close(conn);
        return 0;
    default:
        return -95; /* EOPNOTSUPP */
    }
}

int connection_recover(struct connection *conn, bool transport_ready)
{
    if (conn == NULL || conn->state != CONN_RECOVERING) {
        return -22;
    }
    while (!transport_ready && conn->retry_count < 3) {
        conn->retry_count++;
        fprintf(stderr, "RECOVERY_RETRY count=%u\n", conn->retry_count);
    }
    if (!transport_ready) {
        conn->state = CONN_CLOSED;
        fprintf(stderr, "RECOVERY_EXHAUSTED\n");
        return -110; /* ETIMEDOUT */
    }
    conn->state = CONN_LIVE;
    return 0;
}
