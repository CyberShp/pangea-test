#include "storage.h"
#include <stdio.h>

int submit_request(struct connection *conn, bool transport_error)
{
    int rc;
    if (conn == NULL || conn->state != CONN_LIVE) {
        return -107; /* ENOTCONN */
    }
    conn->inflight++;
    if (transport_error) {
        rc = connection_handle_event(conn, 1);
        if (rc != 0) {
            fprintf(stderr, "TRANSPORT_DOWN_PROPAGATION_FAILED rc=%d\n", rc);
            return rc; /* Deliberate fixture defect: inflight is not decremented. */
        }
        return -104; /* ECONNRESET; inflight also remains elevated. */
    }
    conn->inflight--;
    return 0;
}
