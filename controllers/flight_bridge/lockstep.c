// The flight bridge's per-step loop (flight_bridge.py, IrisBridge._handle_sitl) in C: strict lockstep with ArduPilot
// SITL, one sensor packet per physics step and one control per step. It computes what the Python loop does, through
// the same Webots C API the Python controller module wraps; Python still runs sensing, called after each step.
// Built by ./ironlark into .cache/flight_lockstep.so; the bridge runs its Python loop without it.

#include <webots/accelerometer.h>
#include <webots/gps.h>
#include <webots/gyro.h>
#include <webots/inertial_unit.h>
#include <webots/motor.h>
#include <webots/robot.h>

#include <arpa/inet.h>
#include <math.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>

typedef void (*AfterStep)(void);

// WebotsArduVehicle._get_fdm_struct: time, gyro, accelerometer, roll-pitch-yaw, GPS velocity and position, the
// last two axes negated (FLU to FRD).
static void fdm(double packet[16], WbDeviceTag gyro, WbDeviceTag accel, WbDeviceTag imu, WbDeviceTag gps) {
  const double *const g = wb_gyro_get_values(gyro);
  const double *const a = wb_accelerometer_get_values(accel);
  const double *const i = wb_inertial_unit_get_roll_pitch_yaw(imu);
  const double *const velocity = wb_gps_get_speed_vector(gps);
  const double *const position = wb_gps_get_values(gps);
  const double *const vectors[5] = {g, a, i, velocity, position};
  packet[0] = wb_robot_get_time();
  for (int k = 0; k < 5; ++k) {
    packet[1 + 3 * k] = vectors[k][0];
    packet[2 + 3 * k] = -vectors[k][1];
    packet[3 + 3 * k] = -vectors[k][2];
  }
}

static void send_fdm(int sock, const struct sockaddr_in *sitl, WbDeviceTag gyro, WbDeviceTag accel, WbDeviceTag imu,
                     WbDeviceTag gps) {
  double packet[16];
  fdm(packet, gyro, accel, imu, gps);
  sendto(sock, packet, sizeof(packet), 0, (const struct sockaddr *)sitl, sizeof(*sitl));
}

static int readable(int sock, double seconds) {
  fd_set set;
  FD_ZERO(&set);
  FD_SET(sock, &set);
  struct timeval timeout = {(time_t)seconds, (suseconds_t)((seconds - (time_t)seconds) * 1e6)};
  return select(sock + 1, &set, NULL, NULL, &timeout) > 0;
}

// Returns when Webots ends the simulation.
int ironlark_lockstep(int sock, const char *sitl_address, int port, int timestep, const int *motors, int motor_count,
                      int gyro, int accel, int imu, int gps, AfterStep after_step) {
  struct sockaddr_in sitl;
  memset(&sitl, 0, sizeof(sitl));
  sitl.sin_family = AF_INET;
  sitl.sin_port = htons(port + 1);
  inet_pton(AF_INET, sitl_address, &sitl.sin_addr);
  float command[16];
  for (;;) {
    send_fdm(sock, &sitl, gyro, accel, imu, gps);
    int received = 0;
    while (!received) {
      if (!readable(sock, 1.0)) {
        // A lost sensor packet would stall both sides; resend it.
        send_fdm(sock, &sitl, gyro, accel, imu, gps);
        continue;
      }
      // Keep only the newest control; older ones are SITL's resends.
      while (readable(sock, 0.0)) {
        char data[512];
        const ssize_t size = recv(sock, data, sizeof(data), 0);
        if (size >= (ssize_t)sizeof(command)) {
          memcpy(command, data, sizeof(command));
          received = 1;
        }
      }
    }
    // IrisBridge._handle_controls then WebotsArduVehicle._handle_controls: disabled outputs (-1) stop the motor,
    // thrust is linearized (square root) and scaled by the motor's maximum velocity.
    for (int k = 0; k < motor_count; ++k) {
      const double value = command[k] > 0.0 ? (double)command[k] : 0.0;
      const double linearized = value > 0.0 ? sqrt(value) : 0.0;
      wb_motor_set_velocity((WbDeviceTag)motors[k], linearized * wb_motor_get_max_velocity((WbDeviceTag)motors[k]));
    }
    if (wb_robot_step(timestep) == -1)
      return 0;
    if (after_step)
      after_step();
  }
}
