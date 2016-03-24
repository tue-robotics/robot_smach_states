#!/usr/bin/env python

import smach, rospy, sys
from robot_smach_states.util.startup import startup
import robot_smach_states as states

import threading
import time
import itertools

import math
from visualization_msgs.msg import Marker

from cb_planner_msgs_srvs.msg import *

from robot_skills.util import transformations, msg_constructors


class FollowOperator(smach.State):
    def __init__(self, robot, ask_follow=True, operator_radius=1, timeout=1.0, start_timeout=10, operator_timeout = 20, distance_threshold = 4.0, lost_timeout = 5, lost_distance = 1.5):
        smach.State.__init__(self, outcomes=["stopped",'lost_operator', "no_operator"])
        self._robot = robot
        self._time_started = None
        self._operator = None
        self._operator_id = None
        self._operator_radius = operator_radius
        # self._timeout = timeout
        self._start_timeout = start_timeout
        self._breadcrumbs = []
        self._breadcrumb_distance = 0.1 # meters between dropped breadcrumbs
        self._lost_time = None

        self._at_location = False
        self._first_time_at_location = None
        self._operator_timeout = operator_timeout
        self._distance_threshold = distance_threshold
        self._last_pose_stamped = None
        self._ask_follow = ask_follow
        self._lost_timeout = lost_timeout
        self._lost_distance = lost_distance

        self._operator_pub = rospy.Publisher('/%s/follow_operator/operator_position' % robot.robot_name, geometry_msgs.msg.PointStamped, queue_size=10)
        self._plan_marker_pub = rospy.Publisher('/%s/global_planner/visualization/markers/global_plan' % robot.robot_name, Marker, queue_size=10)
        self._breadcrumb_pub = rospy.Publisher('/%s/follow_operator/breadcrumbs' % robot.robot_name, Marker, queue_size=10)

    def _register_operator(self):
        start_time = rospy.Time.now()

        self._robot.head.look_at_standing_person()

        operator = None
        while not operator:
            if (rospy.Time.now() - start_time).to_sec() > self._operator_timeout:
                return False

            if self._ask_follow:
                self._robot.speech.speak("Should I follow you?", block=True)
                answer = self._robot.ears.recognize("(yes|no)", {})

                if answer:
                    if answer.result == "yes":
                        operator = self._robot.ed.get_closest_entity(radius=1, center_point=msg_constructors.PointStamped(x=1.0, y=0, z=1, frame_id="/%s/base_link"%self._robot.robot_name))
                        if not operator:
                            self._robot.speech.speak("Please stand in front of me")
                    elif answer.result == "no":
                        return False
                    else:
                        rospy.sleep(2)
                else:
                    self._robot.speech.speak("Something is wrong with my ears, please take a look!")
                    return False
            else:
                operator = self._robot.ed.get_closest_entity(radius=1, center_point=msg_constructors.PointStamped(x=1.5, y=0, z=1, frame_id="/%s/base_link"%self._robot.robot_name))
                if not operator:
                    rospy.sleep(1)

        # Operator is None?
        print "We have a new operator: %s"%operator.id
        self._robot.speech.speak("Ok, I will follow you!", block=False)
        self._operator_id = operator.id
        self._breadcrumbs.append(operator)

        self._robot.head.close()

        return True

    def _update_breadcrumb_path(self):
        ''' If the last breadcrumb is less than a threshold away, replace
        the last breadcrumb with the latest operator position; otherwise
        just add it. '''
        if self._operator_id:
            if self._breadcrumbs:
                dx = self._breadcrumbs[-1].pose.position.x - self._operator.pose.position.x
                dy = self._breadcrumbs[-1].pose.position.y - self._operator.pose.position.y
                if math.hypot(dx,dy) < self._breadcrumb_distance :
                    self._breadcrumbs[-1] = self._operator
                else:
                    self._breadcrumbs.append(self._operator)
            else:
                self._breadcrumbs.append(self._operator)

        ''' Remove 'reached' breadcrumbs from breadcrumb path'''
        robot_position = self._robot.base.get_location().pose.position
        temp_crumbs = []
        for crumb in self._breadcrumbs:
            dx = crumb.pose.position.x - robot_position.x
            dy = crumb.pose.position.y - robot_position.y
            if math.hypot(dx,dy) > self._operator_radius:
                temp_crumbs.append(crumb)

        self._breadcrumbs = temp_crumbs

        self._visualize_breadcrumbs()


    def _track_operator(self):
        if self._operator_id:
            self._operator = self._robot.ed.get_entity( id=self._operator_id )
        else:
            self._operator = None

        if self._operator:
            # If the operator is still tracked, it is also the last_operator
            self._last_operator = self._operator

            operator_pos = geometry_msgs.msg.PointStamped()
            operator_pos.header.stamp = rospy.get_rostime()
            operator_pos.header.frame_id = self._operator_id
            operator_pos.point.x = 0.0;
            operator_pos.point.y = 0.0;
            operator_pos.point.z = 0.0;
            self._operator_pub.publish(operator_pos)
            return True
        else:
            # If the operator is lost, check if we still have an ID
            if self._operator_id:
                # At the moment when the operator is lost, tell him to slow down and clear operator ID
                self._operator_id = None
                self._lost_time = rospy.Time.now()
                self._robot.speech.speak("Not so fast!", block=False)
            return False

    def _visualize_breadcrumbs(self):
        breadcrumbs_msg = Marker()
        breadcrumbs_msg.type = Marker.POINTS
        breadcrumbs_msg.scale.x = 0.05
        breadcrumbs_msg.header.stamp = rospy.get_rostime()
        breadcrumbs_msg.header.frame_id = "/map"
        breadcrumbs_msg.color.a = 1
        breadcrumbs_msg.color.r = 0
        breadcrumbs_msg.color.g = 1
        breadcrumbs_msg.color.b = 1
        breadcrumbs_msg.lifetime = rospy.Time(1.0)
        breadcrumbs_msg.id = 0
        breadcrumbs_msg.action = Marker.ADD

        for crumb in self._breadcrumbs:
            breadcrumbs_msg.points.append(crumb.pose.position)

        self._breadcrumb_pub.publish(breadcrumbs_msg)

    def _visualize_plan(self, path):
        line_strip = Marker()
        line_strip.type = Marker.LINE_STRIP
        line_strip.scale.x = 0.05
        line_strip.header.frame_id = "/map"
        line_strip.header.stamp = rospy.Time.now()
        line_strip.color.a = 1
        line_strip.color.r = 0
        line_strip.color.g = 1
        line_strip.color.b = 1
        line_strip.id = 0
        line_strip.action = Marker.ADD

        # Push back all pnts
        for pose_stamped in path:
            line_strip.points.append(pose_stamped.pose.position)

        self._plan_marker_pub.publish(line_strip)


    def _update_navigation(self):
        self._robot.head.cancel_goal()

        robot_position = self._robot.base.get_location().pose.position
        operator_position = self._last_operator.pose.position

        ''' Define end goal constraint, solely based on the (old) operator position '''
        p = PositionConstraint()
        p.constraint = "(x-%f)^2 + (y-%f)^2 < %f^2"% (operator_position.x, operator_position.y, self._operator_radius)

        o = OrientationConstraint()
        if self._operator_id:
            o.frame = self._operator_id
        else:
            o.frame = 'map'
            o.look_at = self._last_operator.pose.position # TODO point where operator was last seen? Or maybe just last tangent of breadcrumb path?

        ''' Determine if the goal has been reached. If it has, return True '''
        dx = operator_position.x - robot_position.x
        dy = operator_position.y - robot_position.y
        length = math.hypot(dx, dy)

        # TODO: Only return True if we exceeded the standstill timeout?
        if length < self._operator_radius:
            if (self._robot.base.get_location().header.stamp - self._time_started).to_sec() > self._start_timeout:
                return True

        ''' Calculate global plan from robot position, through breadcrumbs, to the operator '''
        res = 0.05
        plan = []
        previous_point = robot_position

        for crumb in self._breadcrumbs:
            dx = crumb.pose.position.x - previous_point.x
            dy = crumb.pose.position.y - previous_point.y
            length = math.hypot(dx, dy)

            dx_norm = dx / length
            dy_norm = dy / length
            yaw = math.atan2(dy, dx)

            start = 0
            end = int( length / res)

            for i in range(start, end):
                x = previous_point.x + i * dx_norm * res
                y = previous_point.y + i * dy_norm * res
                plan.append(msg_constructors.PoseStamped(x = x, y = y, z = 0, yaw = yaw))

            previous_point = crumb.pose.position

        # Delete the elements from the plan within the operator radius
        cutoff = int(self._operator_radius/(2.0*res))
        if len(plan) > cutoff:
            del plan[-cutoff:]

        ''' Check if plan is valid. If not, remove invalid points from the path '''
        if not self._robot.base.global_planner.checkPlan(plan):
            print "Breadcrumb plan is blocked"
            # Go through plan from operator to robot and pick the first unoccupied point as goal point
            plan_found = False
            plan = [point for point in plan if self._robot.base.global_planner.checkPlan([point])]



        self._visualize_plan(plan)
        self._robot.base.local_planner.setPlan(plan, p, o)

        return False

    # def _update_navigation(self, breadcrumbs):
    #     self._robot.head.cancel_goal()

    #     goal = breadcrumbs[-1]

    #     # Get the point of the operator and the robot in map frame
    #     r_point = self._robot.base.get_location().pose.position
    #     o_point = goal.pose.position

    #     p = PositionConstraint()
    #     p.constraint = "x^2 + y^2 < %f^2"% self._operator_radius
    #     p.frame = self._operator_id

    #     # Get the distance
    #     dx = o_point.x - r_point.x
    #     dy = o_point.y - r_point.y
    #     length = math.hypot(dx, dy)

    #     standing_still = False

    #     # Store pose if changed and check timeout
    #     current_pose_stamped = self._robot.base.get_location()
    #     if not self._last_pose_stamped:
    #         self._last_pose_stamped = current_pose_stamped
    #     else:
    #         # Compare the pose with the last pose and update if difference is larger than x
    #         if math.hypot(current_pose_stamped.pose.position.x - self._last_pose_stamped.pose.position.x, current_pose_stamped.pose.position.y - self._last_pose_stamped.pose.position.y) > 0.05:
    #             # Update the last pose
    #             print "Last pose stamped (%f,%f) at %f secs"%(self._last_pose_stamped.pose.position.x, self._last_pose_stamped.pose.position.y, self._last_pose_stamped.header.stamp.secs)
    #             self._last_pose_stamped = current_pose_stamped
    #         else:
    #             print "We are standing still :/"

    #             print "Seconds not moved: %f"%(current_pose_stamped.header.stamp - self._last_pose_stamped.header.stamp).to_sec()
    #             print "Seconds since start: %f"%(current_pose_stamped.header.stamp - self._time_started).to_sec()
    #             # Check whether we passed the timeout
    #             if (current_pose_stamped.header.stamp - self._last_pose_stamped.header.stamp).to_sec() > self._timeout:
    #                 print "We are standing still long enough"
    #                 # Only return True if we exceeded the start timeout
    #                 standing_still = True
    #                 if (current_pose_stamped.header.stamp - self._time_started).to_sec() > self._start_timeout:
    #                     print "We passed start timeout"
    #                     if length < self._operator_radius:
    #                         print "Distance to goal < 1.0 : %f" % length
    #                         return True

    #     # Generate a plan through all breadcrumbs
    #     plan = None
    #     res = 0.05
    #     plan = []
    #     previous_point = breadcrumbs[0].pose.position

    #     for crumb in itertools.islice( breadcrumbs , 1, None ):
    #         dx = crumb.pose.position.x - previous_point.x
    #         dy = crumb.pose.position.y - previous_point.y
    #         length = math.hypot(dx, dy)

    #         dx_norm = dx / length
    #         dy_norm = dy / length
    #         yaw = math.atan2(dy, dx)

    #         start = 0
    #         end = int( length / res)

    #         if end > 20:
    #             end -= 20

    #         for i in range(start, end):
    #             x = previous_point.x + i * dx_norm * res
    #             y = previous_point.y + i * dy_norm * res
    #             plan.append(msg_constructors.PoseStamped(x = x, y = y, z = 0, yaw = yaw))

    #         previous_point = crumb.pose.position

    #     if not plan:
    #         yaw = math.atan2(dy, dx)
    #         plan.append(msg_constructors.PoseStamped(x = o_point.x, y = o_point.y, z = 0, yaw = yaw ))

    #     if standing_still and plan:
    #         # Check if plan is blocked
    #         if not self._robot.base.global_planner.checkPlan(plan):
    #             print "Breadcrumb plan is blocked"
    #             # Go through plan from operator to robot and pick the first unoccupied point as goal point
    #             plan_found = False
    #             for point in reversed(plan):
    #                 point_plan = [point]
    #                 if self._robot.base.global_planner.checkPlan(point_plan):
    #                     end_dist_to_operator_x = point.pose.position.x - r_point.x
    #                     end_dist_to_operator_y = point.pose.position.y - r_point.y
    #                     end_dist_to_operator = math.hypot(end_dist_to_operator_x, end_dist_to_operator_y)
    #                     print "Found an unoccupied point on the path %f m from the operator"%end_dist_to_operator
    #                     # If end point inside the local costmap
    #                     if end_dist_to_operator < 2.5:
    #                         plan_found = True
    #                         plan = point_plan
    #                         break
    #                     else:
    #                         print "outside the local costmap"

    #             if not plan_found:
    #                 print "No valid points found. Using the global planner"
    #                 plan = self._robot.base.global_planner.getPlan(p)

    #     if plan:
    #         # Communicate to local planner
    #         o = OrientationConstraint()
    #         o.frame = "map"
    #         o.look_at = self._operator_position


    #     print "Not there yet...\n"
    #     return False # We are not there

    def _recover_operator(self):
        while rospy.Time.now() - self._lost_time < rospy.Duration(self._lost_timeout):
            # Try to catch up with a close entity
            recovered_operator = self._robot.ed.get_closest_entity(radius=self._lost_distance, center_point=self._last_operator.pose.position)
            if recovered_operator:
                break
            rospy.sleep(0.2)

        if recovered_operator:
            self._operator_id = recovered_operator.id
            return True

        return False

    def execute(self, userdata):
        self._robot.head.close()

        if self._robot.robot_name == "amigo":
            self._robot.torso.send_goal('reset', timeout=4.0)

        if not self._register_operator():
            self._robot.base.local_planner.cancelCurrentPlan()
            return "no_operator"

        self._time_started = rospy.Time.now()

        while not rospy.is_shutdown():

            # Track operator
            self._track_operator()
            self._update_breadcrumb_path()

            if self._breadcrumbs:
                # If there are still breadcrumbs on the path, keep following the path (Make sure to remove breadcrumbs when reached!)
                if self._update_navigation():
                    self._robot.base.local_planner.cancelCurrentPlan()
                    self._visualize_breadcrumbs()
                    print "Arrived!"
                    return "stopped"
                else:
                    print "Not there yet..."
            else:
                # If operator is lost, try to recover, if that doesn't work, return lost operator
                if not self._operator:
                    if not self._recover_operator():
                        return "lost_operator"
                else:
                    if (rospy.Time.now() - self._time_started).to_sec() > self._start_timeout:
                        print "Out of breadcrumbs and I still have an operator, I must be there!"
                        return "stopped"

            rospy.sleep(1) # Loop at 1Hz




    # def execute(self, userdata):
    #     self._robot.head.close()
    #     if self._robot.robot_name == "amigo":
    #         self._robot.torso.send_goal('reset', timeout=4.0)

    #     if not self._register_operator():
    #         self._robot.base.local_planner.cancelCurrentPlan()
    #         return "no_operator"

    #     self._time_started = rospy.Time.now()

    #     old_operator = None
    #     breadcrumbs = []

    #     while not rospy.is_shutdown():

    #         # Check if operator present still present
    #         operator = self._get_operator(self._operator_id)

    #         if not breadcrumbs and not operator:
    #             self._robot.speech.speak("I'm out of breadcrumbs. Where is that operator?", block=False)
    #             lost_time = rospy.Time.now()
    #             recovered_operator = None
    #             while rospy.Time.now() - lost_time < rospy.Duration(self._lost_timeout):
    #                 # Try to catch up with a close entity
    #                 recovered_operator = self._robot.ed.get_closest_entity(radius=self._lost_distance, center_point=old_operator.pose.position)
    #                 if recovered_operator:
    #                     break
    #                 rospy.sleep(0.2)

    #             if not recovered_operator:
    #                 self._robot.speech.speak("I lost you", block=True)
    #                 self._robot.base.force_drive(0,0,0,0.5)
    #                 self._robot.base.local_planner.cancelCurrentPlan()
    #                 return "lost_operator"
    #             else:
    #                 print "\n\nWe recovered the operator!\n\n"
    #                 self._robot.speech.speak("Still following you!", block=False)
    #                 self._operator_id = recovered_operator.id
    #                 operator = recovered_operator

    #         old_operator = operator

    #         if operator:
    #             if breadcrumbs:
    #                 # If the operator moved more than .5 meters, lay down a new breadcrumb
    #                 if math.hypot(operator.pose.position.x - breadcrumbs[-1].pose.position.x, operator.pose.position.y - breadcrumbs[-1].pose.position.y) > 0.3:
    #                     breadcrumbs.append(operator)
    #             else:
    #                 breadcrumbs.append(operator)
    #             self._visualize_breadcrumbs(breadcrumbs)

    #         # Update the navigation and check if we are already there
    #         if breadcrumbs:
    #             if self._update_navigation(breadcrumbs):
    #                 self._robot.base.local_planner.cancelCurrentPlan()
    #                 breadcrumbs = []
    #                 self._visualize_breadcrumbs(breadcrumbs)
    #                 if not breadcrumbs:
    #                     return "stopped"
    #         rospy.sleep(1) # Loop at 1Hz

def setup_statemachine(robot):
    sm = smach.StateMachine(outcomes=['Done', 'Aborted'])
    with sm:
        smach.StateMachine.add('TEST', FollowOperator(robot), transitions={"stopped":"TEST",'lost_operator':"TEST", "no_operator":"TEST"})
        return sm

if __name__ == "__main__":
    if len(sys.argv) > 1:
        robot_name = sys.argv[1]
    else:
        print "Please provide robot name as argument."
        exit(1)

    rospy.init_node('test_follow_operator')
    startup(setup_statemachine, robot_name=robot_name)
