#include "worker.h"

static int parse_input(int value) {
    return value + 1;
}

static int handle_request(int value) {
    int parsed = parse_input(value);
    return do_work(parsed);
}

int main(void) {
    return handle_request(41);
}

