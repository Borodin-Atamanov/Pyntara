/*
 * pyntara-wayrecord - Wayland screen capture source for ffmpeg.
 *
 * The ffmpeg CLI cannot capture Wayland natively, so this program is a
 * capture source: it records the screen through the direct KWin screencast
 * protocol (zkde_screencast_unstable_v1) and writes raw frames to stdout.
 * The caller pipes the stream into ffmpeg and controls every encoding
 * parameter, for example:
 *
 *   pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4
 *
 * The protocol is only announced to trusted applications: the task deploys
 * pyntara-wayrecord.desktop with X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1,
 * so KWin grants the interface to this executable. No portal and no screen
 * dialog are involved. The captured frames arrive through a PipeWire node;
 * this program links a consumer stream to that node and writes the raw
 * BGRA frames to stdout at the requested frame rate. All messages go to
 * stderr; stdout carries only raw frames. The capture stops when the pipe
 * closes (ffmpeg exits) or on Ctrl+C.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <sys/mman.h>
#include <sys/poll.h>

#include <wayland-client.h>
#include <pipewire/pipewire.h>
#include <spa/param/param.h>
#include <spa/pod/builder.h>
#include <spa/pod/iter.h>
#include <spa/pod/pod.h>
#include <spa/param/video/raw.h>

#include "zkde-screencast-client.h"

#define PROG "pyntara-wayrecord"
#define STREAM_FD 1
#define DEFAULT_CAPS "video/x-raw,format=BGRA,framerate=30/1"
#define CREATE_TIMEOUT_MS 10000
#define CONNECT_TIMEOUT_MS 5000

static const char DESCRIPTION[] =
    "Capture the Wayland screen as raw video on stdout.\n"
    "Records the screen through the direct KWin screencast protocol\n"
    "(trusted application) and writes raw BGRA frames to stdout, so the\n"
    "caller pipes the stream into ffmpeg and controls every encoding\n"
    "parameter. No portal and no screen dialog.\n";

static const char EXAMPLES[] =
    "examples:\n"
    "  record the whole screen at 30 fps (default):\n"
    "    pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -r 30 -i pipe:0 -c:v libx264 out.mp4\n"
    "\n"
    "  slow motion by dropping frames, one per two seconds (timelapse):\n"
    "    pyntara-wayrecord --caps video/x-raw,format=BGRA,framerate=1/2 | ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -r 1/2 -i pipe:0 -c:v libx264 out.mp4\n"
    "\n"
    "  hardware encoding with conversion done by ffmpeg:\n"
    "    pyntara-wayrecord | ffmpeg -f rawvideo -pix_fmt bgra -s 1920x1080 -r 30 -i pipe:0 -vf format=nv12,hwupload -c:v hevc_vaapi out.mp4\n"
    "\n"
    "  stop by closing the pipe (for example ffmpeg -t) or with Ctrl+C\n";

/* Rate limiter state, in nanoseconds since boot. */
static uint64_t next_emit_ns = 0;
static uint64_t emit_period_ns = 0;

/* Resolved caps for the stderr report. */
static const char *caps_format = "BGRA";
static int caps_width = 0;
static int caps_height = 0;
static uint32_t caps_fps_num = 30;
static uint32_t caps_fps_den = 1;

/* Set by signal handlers to stop the main loop. */
static volatile sig_atomic_t stop_requested = 0;

static void on_stop_signal(int signum) {
    (void)signum;
    stop_requested = 1;
}

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/*
 * Wayland side: bind the screencast global and stream the first output.
 */

static struct wl_display *wl_disp = NULL;
static uint32_t wl_output_name = 0;
static uint32_t screencast_name = 0;
static int have_screencast = 0;
static int stream_node = -1;
static char stream_error[512] = "";

static void wl_registry_global(void *data, struct wl_registry *registry,
                               uint32_t name, const char *interface,
                               uint32_t version) {
    (void)data;
    (void)version;
    if (strcmp(interface, "wl_output") == 0 && wl_output_name == 0) {
        wl_output_name = name;
    } else if (strcmp(interface, "zkde_screencast_unstable_v1") == 0) {
        have_screencast = 1;
        screencast_name = name;
    }
}

static void wl_registry_global_remove(void *data, struct wl_registry *registry,
                                      uint32_t name) {
    (void)data;
    (void)registry;
    (void)name;
}

static const struct wl_registry_listener wl_registry_listener = {
    wl_registry_global,
    wl_registry_global_remove,
};

static void stream_created(void *data,
                           struct zkde_screencast_stream_unstable_v1 *s,
                           uint32_t node) {
    (void)data;
    (void)s;
    stream_node = (int)node;
}

static void stream_closed(void *data,
                          struct zkde_screencast_stream_unstable_v1 *s) {
    (void)data;
    (void)s;
    fprintf(stderr, "error: screencast stream closed\n");
    stop_requested = 1;
}

static void stream_failed(void *data,
                          struct zkde_screencast_stream_unstable_v1 *s,
                          const char *error) {
    (void)data;
    (void)s;
    snprintf(stream_error, sizeof(stream_error), "%s", error ? error : "unknown");
    stop_requested = 1;
}

static const struct zkde_screencast_stream_unstable_v1_listener stream_listener = {
    stream_closed,
    stream_created,
    stream_failed,
};

/* Returns 0 and sets stream_node on success, or -1 with an error message. */
static int wayland_start_stream(const char *app_id) {
    (void)app_id;
    wl_disp = wl_display_connect(NULL);
    if (!wl_disp) {
        fprintf(stderr, "error: cannot connect to the Wayland display\n");
        return -1;
    }
    struct wl_registry *registry = wl_display_get_registry(wl_disp);
    wl_registry_add_listener(registry, &wl_registry_listener, NULL);
    wl_display_roundtrip(wl_disp);
    if (!have_screencast) {
        fprintf(stderr,
                "error: the KWin screencast interface is not granted\n"
                "install pyntara-wayrecord.desktop with "
                "X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1 and "
                "re-run the ffmpeg_setup task\n");
        return -1;
    }
    if (wl_output_name == 0) {
        fprintf(stderr, "error: no wl_output available\n");
        return -1;
    }
    struct wl_registry *reg2 = wl_display_get_registry(wl_disp);
    struct wl_output *output = wl_registry_bind(reg2, wl_output_name, &wl_output_interface, 4);
    struct wl_registry *reg3 = wl_display_get_registry(wl_disp);
    struct zkde_screencast_unstable_v1 *sc =
        wl_registry_bind(reg3, screencast_name, &zkde_screencast_unstable_v1_interface, 1);
    struct zkde_screencast_stream_unstable_v1 *stream =
        zkde_screencast_unstable_v1_stream_output(sc, output, 0);
    zkde_screencast_stream_unstable_v1_add_listener(stream, &stream_listener, NULL);
    (void)stream;
    wl_display_flush(wl_disp);

    struct pollfd pfd = {.fd = wl_display_get_fd(wl_disp), .events = POLLIN};
    int waited_ms = 0;
    while (stream_node < 0 && !stop_requested && waited_ms < CREATE_TIMEOUT_MS) {
        if (poll(&pfd, 1, 200) > 0) {
            wl_display_dispatch(wl_disp);
        }
        waited_ms += 200;
    }
    if (stream_node < 0) {
        if (stream_error[0]) {
            fprintf(stderr, "error: screencast failed: %s\n", stream_error);
        } else {
            fprintf(stderr, "error: no screencast stream within %d ms\n", CREATE_TIMEOUT_MS);
        }
        return -1;
    }
    fprintf(stderr, "screencast node: %d\n", stream_node);
    return 0;
}

/*
 * PipeWire side: find the object.serial of the node, link a consumer
 * stream to it and write frames to stdout.
 */

static struct pw_main_loop *pw_loop_handle = NULL;
static struct pw_stream *pw_stream_handle = NULL;
static int found_serial = -1;
static int have_width = 0;
static int have_height = 0;
static unsigned long frame_count = 0;

static void pw_registry_global(void *data, uint32_t id, uint32_t permissions,
                               const char *type, uint32_t version,
                               const struct spa_dict *props) {
    (void)data;
    (void)permissions;
    (void)version;
    if (strcmp(type, PW_TYPE_INTERFACE_Node) != 0) {
        return;
    }
    if (id != (uint32_t)stream_node) {
        return;
    }
    const char *serial = spa_dict_lookup(props, PW_KEY_OBJECT_SERIAL);
    if (serial) {
        found_serial = (int)strtoul(serial, NULL, 10);
    }
    const char *name = spa_dict_lookup(props, PW_KEY_MEDIA_NAME);
    fprintf(stderr, "screencast source: %s\n", name ? name : "unknown");
}

static const struct pw_registry_events pw_registry_events_impl = {
    PW_VERSION_REGISTRY_EVENTS,
    .global = pw_registry_global,
};

static void pw_stream_state_changed(void *userdata, enum pw_stream_state old,
                                    enum pw_stream_state state, const char *error) {
    (void)userdata;
    (void)old;
    if (state == PW_STREAM_STATE_ERROR) {
        fprintf(stderr, "error: pipewire stream failed: %s\n", error ? error : "unknown");
        stop_requested = 1;
    }
}

static void pw_stream_param_changed(void *userdata, uint32_t id,
                                    const struct spa_pod *param) {
    (void)userdata;
    if (id != SPA_PARAM_Format || !param) {
        return;
    }
    struct spa_pod_object *pod = (struct spa_pod_object *)param;
    struct spa_pod_prop *prop = NULL;
    SPA_POD_OBJECT_FOREACH(pod, prop) {
        if (prop->key == SPA_FORMAT_VIDEO_size) {
            const struct spa_rectangle *r =
                (const struct spa_rectangle *)SPA_POD_BODY(&prop->value);
            have_width = (int)r->width;
            have_height = (int)r->height;
        }
    }
}

static void pw_stream_process(void *userdata) {
    (void)userdata;
    struct pw_buffer *buf = pw_stream_dequeue_buffer(pw_stream_handle);
    if (!buf) {
        return;
    }
    struct spa_buffer *sb = buf->buffer;
    if (sb->n_datas > 0) {
        uint64_t now = now_ns();
        if (now >= next_emit_ns) {
            struct spa_data *sd = &sb->datas[0];
            void *mapped = NULL;
            if (sd->type == SPA_DATA_MemPtr) {
                mapped = sd->data;
            } else if (sd->type == SPA_DATA_MemFd) {
                mapped = mmap(NULL, sd->maxsize, PROT_READ, MAP_SHARED, sd->fd, 0);
                if (mapped == MAP_FAILED) {
                    mapped = NULL;
                }
            }
            if (mapped) {
                size_t written = 0;
                size_t total = sd->chunk->size;
                char *data = (char *)mapped + sd->chunk->offset;
                while (written < total) {
                    ssize_t n = write(STREAM_FD, data + written, total - written);
                    if (n < 0) {
                        if (errno == EPIPE) {
                            stop_requested = 1;
                            break;
                        }
                        if (errno == EINTR) {
                            continue;
                        }
                        fprintf(stderr, "error: cannot write frame: %s\n", strerror(errno));
                        stop_requested = 1;
                        break;
                    }
                    written += (size_t)n;
                }
                if (written == total) {
                    frame_count++;
                }
                if (sd->type == SPA_DATA_MemFd) {
                    munmap(mapped, sd->maxsize);
                }
            }
            if (emit_period_ns > 0) {
                next_emit_ns = now + emit_period_ns;
            }
        }
    }
    pw_stream_queue_buffer(pw_stream_handle, buf);
}

static const struct pw_stream_events pw_stream_events_impl = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = pw_stream_state_changed,
    .param_changed = pw_stream_param_changed,
    .process = pw_stream_process,
};

static int pipewire_run(void) {
    pw_init(NULL, NULL);
    pw_loop_handle = pw_main_loop_new(NULL);
    struct pw_loop *loop = pw_main_loop_get_loop(pw_loop_handle);
    struct pw_context *context = pw_context_new(loop, NULL, 0);
    struct pw_core *core = pw_context_connect(context, NULL, 0);
    if (!core) {
        fprintf(stderr, "error: cannot connect to the PipeWire daemon\n");
        return -1;
    }

    struct pw_registry *registry = pw_core_get_registry(core, PW_VERSION_REGISTRY, 0);
    struct spa_hook registry_hook;
    pw_registry_add_listener(registry, &registry_hook, &pw_registry_events_impl, NULL);

    int waited_ms = 0;
    while (found_serial < 0 && waited_ms < CONNECT_TIMEOUT_MS) {
        pw_loop_iterate(loop, 100);
        waited_ms += 100;
    }
    if (found_serial < 0) {
        fprintf(stderr, "error: the screencast node is not in the PipeWire graph\n");
        return -1;
    }
    fprintf(stderr, "linking to screencast node serial %d\n", found_serial);

    struct pw_properties *props = pw_properties_new(NULL, NULL);
    pw_properties_setf(props, PW_KEY_TARGET_OBJECT, "%d", found_serial);
    pw_stream_handle = pw_stream_new(core, "pyntara-wayrecord", props);
    struct spa_hook stream_hook;
    pw_stream_add_listener(pw_stream_handle, &stream_hook, &pw_stream_events_impl, NULL);

    uint8_t pod_buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(pod_buffer, sizeof(pod_buffer));
    const struct spa_pod *params[1];
    params[0] = spa_pod_builder_add_object(
        &b, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA));
    if (pw_stream_connect(pw_stream_handle, PW_DIRECTION_INPUT, PW_ID_ANY,
                          PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_DONT_RECONNECT,
                          params, 1) != 0) {
        fprintf(stderr, "error: cannot connect the capture stream\n");
        return -1;
    }

    fprintf(stderr, "recording started; Ctrl+C or closing the pipe stops it\n");
    while (!stop_requested) {
        pw_loop_iterate(loop, 200);
    }
    fprintf(stderr, "recorded %lu frames\n", frame_count);
    return 0;
}

/*
 * Caps parsing: read format, size and frame rate from a GStreamer-style
 * caps string. The values drive the rate limiter and the ffmpeg hint; the
 * stream itself is always BGRA at the native screen size.
 */

static int parse_fraction(const char *text, uint32_t *num, uint32_t *den) {
    const char *slash = strchr(text, '/');
    if (!slash) {
        return 0;
    }
    char *end = NULL;
    *num = (uint32_t)strtoul(text, &end, 10);
    if (end != slash) {
        return 0;
    }
    *den = (uint32_t)strtoul(slash + 1, NULL, 10);
    return *den != 0;
}

static void parse_caps(const char *caps) {
    caps_format = "BGRA";
    const char *p = strchr(caps, ',');
    while (p) {
        const char *key = p + 1;
        const char *eq = strchr(key, '=');
        if (!eq) {
            break;
        }
        const char *value = eq + 1;
        const char *next = strchr(value, ',');
        size_t key_len = (size_t)(eq - key);
        size_t value_len = next ? (size_t)(next - value) : strlen(value);
        if (key_len == 6 && strncmp(key, "format", 6) == 0) {
            char fmt[16];
            size_t n = value_len < sizeof(fmt) - 1 ? value_len : sizeof(fmt) - 1;
            memcpy(fmt, value, n);
            fmt[n] = '\0';
            if (strcmp(fmt, "BGRA") != 0 && strcmp(fmt, "BGRx") != 0) {
                fprintf(stderr,
                        "warning: format %s is not produced by the screencast "
                        "node; stream is BGRA, convert with ffmpeg -vf format=%s\n",
                        fmt, fmt);
            }
            caps_format = "BGRA";
        } else if (key_len == 5 && strncmp(key, "width", 5) == 0) {
            caps_width = (int)strtol(value, NULL, 10);
        } else if (key_len == 6 && strncmp(key, "height", 6) == 0) {
            caps_height = (int)strtol(value, NULL, 10);
        } else if (key_len == 9 && strncmp(key, "framerate", 9) == 0) {
            char fps[32];
            size_t n = value_len < sizeof(fps) - 1 ? value_len : sizeof(fps) - 1;
            memcpy(fps, value, n);
            fps[n] = '\0';
            if (!parse_fraction(fps, &caps_fps_num, &caps_fps_den)) {
                fprintf(stderr, "warning: cannot parse framerate %s\n", fps);
            }
        }
        p = next;
    }
    if (emit_period_ns == 0 && caps_fps_num > 0) {
        emit_period_ns = (1000000000ull * caps_fps_den) / caps_fps_num;
    }
}

static void print_help(void) {
    printf("%s\n\n%s\n%s", DESCRIPTION, EXAMPLES, "options:\n"
           "  --caps CAPS   GStreamer-style caps: format, width, height and\n"
           "                framerate of the stream. The screen stream is always\n"
           "                BGRA at the native size; framerate drops frames to the\n"
           "                requested rate. Default: " DEFAULT_CAPS "\n"
           "  --help        show this help\n");
}

int main(int argc, char **argv) {
    const char *caps = DEFAULT_CAPS;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            print_help();
            return 0;
        } else if (strcmp(argv[i], "--caps") == 0) {
            if (i + 1 < argc) {
                caps = argv[++i];
            }
        } else {
            fprintf(stderr, "error: unknown argument %s\n", argv[i]);
            print_help();
            return 1;
        }
    }
    parse_caps(caps);

    signal(SIGINT, on_stop_signal);
    signal(SIGTERM, on_stop_signal);
    signal(SIGPIPE, SIG_IGN);

    if (wayland_start_stream("pyntara-wayrecord") != 0) {
        return 1;
    }
    if (pipewire_run() != 0) {
        return 1;
    }

    int width = have_width ? have_width : caps_width;
    int height = have_height ? have_height : caps_height;
    if (!width || !height) {
        width = 1920;
        height = 1080;
    }
    if (caps_width && caps_width != width) {
        fprintf(stderr,
                "warning: requested width %d differs from the screen %d; "
                "resize with ffmpeg -vf scale=%dx%d\n",
                caps_width, width, caps_height, height);
    }
    if (caps_height && caps_height != height) {
        fprintf(stderr,
                "warning: requested height %d differs from the screen %d; "
                "resize with ffmpeg -vf scale=%dx%d\n",
                caps_height, height, caps_width, width);
    }
    char fps_text[32];
    if (caps_fps_den == 1) {
        snprintf(fps_text, sizeof(fps_text), "%u", caps_fps_num);
    } else {
        snprintf(fps_text, sizeof(fps_text), "%u/%u", caps_fps_num, caps_fps_den);
    }
    fprintf(stderr,
            "video caps: BGRA %dx%d %s\n"
            "pipe into ffmpeg, e.g.: pyntara-wayrecord | ffmpeg -f rawvideo "
            "-pix_fmt bgra -s %dx%d -r %s -i pipe:0 ...\n",
            width, height, fps_text, width, height, fps_text);
    return 0;
}
