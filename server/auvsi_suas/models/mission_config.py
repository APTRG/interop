"""Mission configuration model."""

import logging
from auvsi_suas.patches.simplekml_patch import Color
from auvsi_suas.patches.simplekml_patch import AltitudeMode
from fly_zone import FlyZone
from gps_position import GpsPosition
from moving_obstacle import MovingObstacle
from obstacle_access_log import ObstacleAccessLog
from server_info_access_log import ServerInfoAccessLog
from stationary_obstacle import StationaryObstacle
from takeoff_or_landing_event import TakeoffOrLandingEvent
from time_period import TimePeriod
from uas_telemetry import UasTelemetry
from waypoint import Waypoint
from django.contrib.auth.models import User
from django.db import models

# Logging for the module
logger = logging.getLogger(__name__)


class MissionConfig(models.Model):
    """The details for the active mission. There should only be one."""
    # The home position for use as a reference point. Should be the tents.
    home_pos = models.ForeignKey(GpsPosition,
                                 related_name="missionconfig_home_pos")

    # The max distance to a waypoint to consider it satisfied/hit in feet.
    mission_waypoints_dist_max = models.FloatField()

    # The waypoints that define the mission waypoint path
    mission_waypoints = models.ManyToManyField(
        Waypoint,
        related_name="missionconfig_mission_waypoints")

    # The polygon that defines the search grid
    search_grid_points = models.ManyToManyField(
        Waypoint,
        related_name="missionconfig_search_grid_points")

    # The last known position of the emergent target
    emergent_last_known_pos = models.ForeignKey(
        GpsPosition,
        related_name="missionconfig_emergent_last_known_pos")

    # Off-axis target position
    off_axis_target_pos = models.ForeignKey(
        GpsPosition,
        related_name="missionconfig_off_axis_target_pos")

    # The SRIC position
    sric_pos = models.ForeignKey(GpsPosition,
                                 related_name="missionconfig_sric_pos")

    # The IR primary target position
    ir_primary_target_pos = models.ForeignKey(
        GpsPosition,
        related_name="missionconfig_ir_primary_target_pos")

    # The IR secondary target position
    ir_secondary_target_pos = models.ForeignKey(
        GpsPosition,
        related_name="missionconfig_ir_secondary_target_pos")

    # The air drop position
    air_drop_pos = models.ForeignKey(GpsPosition,
                                     related_name="missionconfig_air_drop_pos")

    def __unicode__(self):
        """Descriptive text for use in displays."""
        mission_waypoints_str = ", ".join(
            ["%s" % wpt.__unicode__() for wpt in self.mission_waypoints.all()])
        search_grid_str = ", ".join(
            ["%s" % wpt.__unicode__()
             for wpt in self.search_grid_points.all()])

        return unicode("MissionConfig (pk:%s, home_pos:%s, "
                       "mission_waypoints_dist_max:%s, "
                       "mission_waypoints:[%s], search_grid:[%s], "
                       "emergent_lkp:%s, off_axis:%s, "
                       "sric_pos:%s, ir_primary_pos:%s, ir_secondary_pos:%s, "
                       "air_drop_pos:%s)" %
                       (str(self.pk), self.home_pos.__unicode__(),
                        str(self.mission_waypoints_dist_max),
                        mission_waypoints_str, search_grid_str,
                        self.emergent_last_known_pos.__unicode__(),
                        self.off_axis_target_pos.__unicode__(),
                        self.sric_pos.__unicode__(),
                        self.ir_primary_target_pos.__unicode__(),
                        self.ir_secondary_target_pos.__unicode__(),
                        self.air_drop_pos.__unicode__()))

    def evaluateUasSatisfiedWaypoints(self, uas_telemetry_logs):
        """Determines whether the UAS satisfied the waypoints.

        Args:
            uas_telemetry_logs: A list of UAS Telemetry logs.
        Returns:
            A list of booleans where each value indicates whether the UAS
            satisfied the waypoint for that index.
        """
        waypoints_satisfied = list()
        waypoints = self.mission_waypoints.order_by('order')
        for waypoint in waypoints:
            satisfied = False
            for uas_log in uas_telemetry_logs:
                distance = uas_log.uas_position.distanceTo(waypoint.position)
                if distance < self.mission_waypoints_dist_max:
                    satisfied = True
                    break
            waypoints_satisfied.append(satisfied)
        return waypoints_satisfied

    def evaluateTeams(self):
        """Evaluates the teams (non admin users) of the competition.

        Returns:
            A map from user to evaluate data. The evaluation data has the
            following map structure:
            {
                'waypoints_satisfied': {
                    id: Boolean,
                }
                'out_of_bounds_time': Seconds spent out of bounds,
                'interop_times': {
                    'server_info': {'max': Value, 'avg': Value},
                    'obst_info': {'max': Value, 'avg': Value},
                    'uas_telem': {'max': Value, 'avg': Value},
                },
                'stationary_obst_collision': {
                    id: Boolean
                },
                'moving_obst_collision': {
                    id: Boolean
                }
            }
        """
        # Get base data for mission
        fly_zones = FlyZone.objects.all()
        stationary_obstacles = StationaryObstacle.objects.all()
        moving_obstacles = MovingObstacle.objects.all()

        # Start a results map from user to evaluation data
        results = dict()

        # Fill in evaluation data for each user except admins
        users = User.objects.all()
        logger.info('Starting team evaluations.')

        for user in users:
            # Ignore admins
            if user.is_superuser:
                continue

            logger.info('Evaluation starting for user: %s.' % user.username)

            # Start the evaluation data structure
            eval_data = results.setdefault(user, dict())

            # Get the relevant logs for the user
            flight_periods = TakeoffOrLandingEvent.getFlightPeriodsForUser(
                user)
            # TODO(prattmic): cleanup APIs to all take/return TimePeriod
            fp_timeperiods = [TimePeriod(f[0], f[1]) for f in flight_periods]

            uas_telemetry_logs = UasTelemetry.by_user(user)

            # Determine if the uas hit the waypoints
            waypoints = self.evaluateUasSatisfiedWaypoints(uas_telemetry_logs)
            waypoints_keyed = dict()
            for wpt_id in xrange(len(waypoints)):
                waypoints_keyed[wpt_id + 1] = waypoints[wpt_id]
            eval_data['waypoints_satisfied'] = waypoints_keyed

            # Determine if the uas went out of bounds
            out_of_bounds_time = FlyZone.evaluateUasOutOfBounds(
                fly_zones, uas_telemetry_logs)
            eval_data['out_of_bounds_time'] = out_of_bounds_time

            # Determine interop rates
            interop_times = eval_data.setdefault('interop_times', dict())

            server_info_times = ServerInfoAccessLog.getAccessLogRates(
                flight_periods,
                ServerInfoAccessLog.by_time_period(user, fp_timeperiods))

            obstacle_times = ObstacleAccessLog.getAccessLogRates(
                flight_periods,
                ObstacleAccessLog.by_time_period(user, fp_timeperiods))

            uas_telemetry_times = UasTelemetry.getAccessLogRates(
                flight_periods,
                UasTelemetry.by_time_period(user, fp_timeperiods))

            interop_times['server_info'] = {
                'max': server_info_times[0],
                'avg': server_info_times[1]
            }
            interop_times['obst_info'] = {
                'max': obstacle_times[0],
                'avg': obstacle_times[1]
            }
            interop_times['uas_telem'] = {
                'max': uas_telemetry_times[0],
                'avg': uas_telemetry_times[1]
            }

            # Determine collisions with stationary and moving obstacles
            stationary_collisions = eval_data.setdefault(
                'stationary_obst_collision', dict())
            for obst in stationary_obstacles:
                collision = obst.evaluateCollisionWithUas(uas_telemetry_logs)
                stationary_collisions[obst.pk] = collision

            moving_collisions = eval_data.setdefault(
                'moving_obst_collision', dict())
            for obst in moving_obstacles:
                collision = obst.evaluateCollisionWithUas(uas_telemetry_logs)
                moving_collisions[obst.pk] = collision

        return results

    def toJSON(self):
        """Return a dict, for conversion to JSON."""
        ret = {
            "id": self.pk,
            "home_pos": {
                "latitude": self.home_pos.latitude,
                "longitude": self.home_pos.longitude,
            },
            "mission_waypoints_dist_max": self.mission_waypoints_dist_max,
            "mission_waypoints": [],  # Filled in below
            "search_grid_points": [],  # Filled in below
            "emergent_last_known_pos": {
                "latitude": self.emergent_last_known_pos.latitude,
                "longitude": self.emergent_last_known_pos.longitude,
            },
            "off_axis_target_pos": {
                "latitude": self.off_axis_target_pos.latitude,
                "longitude": self.off_axis_target_pos.longitude,
            },
            "sric_pos": {
                "latitude": self.sric_pos.latitude,
                "longitude": self.sric_pos.longitude,
            },
            "ir_primary_target_pos": {
                "latitude": self.ir_primary_target_pos.latitude,
                "longitude": self.ir_primary_target_pos.longitude,
            },
            "ir_secondary_target_pos": {
                "latitude": self.ir_secondary_target_pos.latitude,
                "longitude": self.ir_secondary_target_pos.longitude,
            },
            "air_drop_pos": {
                "latitude": self.air_drop_pos.latitude,
                "longitude": self.air_drop_pos.longitude,
            },
        }

        for waypoint in self.mission_waypoints.all():
            ret['mission_waypoints'].append({
                "id": waypoint.pk,
                "latitude": waypoint.position.gps_position.latitude,
                "longitude": waypoint.position.gps_position.longitude,
                "altitude_msl": waypoint.position.altitude_msl,
                "order": waypoint.order,
            })

        for point in self.search_grid_points.all():
            ret['search_grid_points'].append({
                "id": point.pk,
                "latitude": point.position.gps_position.latitude,
                "longitude": point.position.gps_position.longitude,
                "altitude_msl": point.position.altitude_msl,
                "order": point.order,
            })

        return ret

    @classmethod
    def kml_all(cls, kml):
        """
        Appends kml nodes describing all mission configurations.

        Args:
            kml: A simpleKML Container to which the mission data will be added
        """
        for mission in MissionConfig.objects.all():
            mission.kml(kml)

    def kml(self, kml):
        """
        Appends kml nodes describing this mission configurations.

        Args:
            kml: A simpleKML Container to which the mission data will be added
        """
        mission_name = 'Mission {}'.format(self.pk)
        kml_folder = kml.newfolder(name=mission_name)

        # Static Points
        locations = {
            'Home Position': self.home_pos,
            'Emergent LKP': self.emergent_last_known_pos,
            'Off Axis': self.off_axis_target_pos,
            'SRIC': self.sric_pos,
            'IR Primary': self.ir_primary_target_pos,
            'IR Secondary': self.ir_secondary_target_pos,
            'Air Drop': self.air_drop_pos,
        }
        for key, point in locations.iteritems():
            gps = (point.longitude, point.latitude)
            wp = kml_folder.newpoint(name=key, coords=[gps])
            wp.description = str(point)

        # Waypoints
        waypoints_folder = kml_folder.newfolder(name='Waypoints')
        linestring = waypoints_folder.newlinestring(name="Waypoints")
        waypoints = []
        waypoint_num = 1
        for waypoint in self.mission_waypoints.all():
            gps = waypoint.position.gps_position
            coord = (gps.longitude, gps.latitude,
                     waypoint.position.altitude_msl)
            waypoints.append(coord)

            # Add waypoint marker
            wp = waypoints_folder.newpoint(name=str(waypoint_num),
                                           coords=[coord])
            wp.description = str(waypoint)
            wp.altitudemode = AltitudeMode.absolute
            wp.extrude = 1
            wp.visibility = False
            waypoint_num += 1
        linestring.coords = waypoints

        # Waypoints Style
        linestring.altitudemode = AltitudeMode.absolute
        linestring.extrude = 1
        linestring.style.linestyle.color = Color.black
        linestring.style.polystyle.color = Color.changealphaint(100,
                                                                Color.green)

        # Search Area
        search_area_folder = kml_folder.newfolder(name='Search Area')
        pol = search_area_folder.newpolygon(name='Search Area')
        search_area = []
        search_area_num = 1
        for point in self.search_grid_points.all():
            gps = point.position.gps_position
            coord = (gps.longitude, gps.latitude, point.position.altitude_msl)
            search_area.append(coord)

            # Add boundary marker
            wp = search_area_folder.newpoint(name=str(search_area_num),
                                             coords=[coord])
            wp.description = str(point)
            wp.visibility = False
            search_area_num += 1
        search_area.append(search_area[0])
        pol.outerboundaryis = search_area

        # Search Area Style
        pol.style.linestyle.color = Color.black
        pol.style.linestyle.width = 2
        pol.style.polystyle.color = Color.changealphaint(50, Color.blue)
