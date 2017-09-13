import math
import numpy as np
import Box2D
from Box2D.b2 import (edgeShape, circleShape, fixtureDef, polygonShape, revoluteJointDef, contactListener)
import gym
from gym import spaces
from gym.utils import seeding

# Rocket trajectory optimization is a classic topic in Optimal Control.
CONTINUOUS = False

FPS = 50

INITIAL_RANDOM = 1.0

START_FUEL = 300.0

# ROCKET
ROCKET_RATIO = 12
ROCKET_WIDTH = 3.7
ROCKET_HEIGHT = 40.0
ENGINE_HEIGHT = ROCKET_WIDTH * 0.5
ENGINE_WIDTH = ENGINE_HEIGHT * 0.7
MAIN_ENGINE_POWER = 870000
GIMBAL_THRESHOLD = 0.4
LANDER_POLY = [
    (-ROCKET_WIDTH / 2, 0),
    (+ROCKET_WIDTH / 2, 0),
    (ROCKET_WIDTH / 2, +ROCKET_HEIGHT),
    (-ROCKET_WIDTH / 2, +ROCKET_HEIGHT)
]
ENGINE_POLY = [
    (0, 0),
    (ENGINE_WIDTH / 2, -ENGINE_HEIGHT),
    (-ENGINE_WIDTH / 2, -ENGINE_HEIGHT)
]

# LEGS
LEG_LENGTH = ROCKET_WIDTH * 3
BASE_ANGLE = -0.55
SPRING_ANGLE = 0.1
LEG_AWAY = ROCKET_WIDTH / 2
LEG_SPRING_TORQUE = 40


def LEG_POLY(i):
    return ([0, 0], [0, LEG_LENGTH / 25], [i * LEG_LENGTH, 0], [i * LEG_LENGTH, -LEG_LENGTH / 20],
            [i * LEG_LENGTH / 3, -LEG_LENGTH / 7])


BARGE_HEIGHT = ROCKET_WIDTH * 2
BARGE_WIDTH = BARGE_HEIGHT * 10

VIEWPORT_W = 900
VIEWPORT_H = 1200
W = VIEWPORT_W
H = VIEWPORT_H


class ContactDetector(contactListener):
    def __init__(self, env):
        contactListener.__init__(self)
        self.env = env

    def BeginContact(self, contact):
        if self.env.water in [contact.fixtureA.body, contact.fixtureB.body] \
                or self.env.lander in [contact.fixtureA.body, contact.fixtureB.body]:
            self.env.game_over = True
        else:
            for i in range(2):
                if self.env.legs[i] in [contact.fixtureA.body, contact.fixtureB.body]:
                    self.env.legs[i].ground_contact = True

    def EndContact(self, contact):
        for i in range(2):
            if self.env.legs[i] in [contact.fixtureA.body, contact.fixtureB.body]:
                self.env.legs[i].ground_contact = False


class RocketLander(gym.Env):
    metadata = {
        'render.modes': ['human', 'rgb_array'],
        'video.frames_per_second': FPS
    }

    def __init__(self):
        self._seed()
        self.viewer = None

        self.world = Box2D.b2World()
        self.water = None
        self.lander = None
        self.engine = None
        self.barge = None
        self.legs = []

        high = np.array([np.inf] * 10)  # useful range is -1 .. +1, but spikes can be higher
        self.observation_space = spaces.Box(-high, high)

        if CONTINUOUS:
            # Action is two floats [main engine, left-right engines].
            # Main engine: -1..0 off, 0..+1 throttle from 50% to 100% power. Engine can't work with less than 50% power.
            # Left-right:  -1.0..-0.5 fire left engine, +0.5..+1.0 fire right engine, -0.5..0.5 off
            self.action_space = spaces.Box(-1, +1, (2,))
        else:
            self.action_space = spaces.Discrete(5)

        self._reset()

    def _seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def _destroy(self):
        if not self.water: return
        self.world.contactListener = None
        self.world.DestroyBody(self.water)
        self.water = None
        self.world.DestroyBody(self.lander)
        self.lander = None
        self.world.DestroyBody(self.barge)
        self.barge = None
        self.world.DestroyBody(self.legs[0])
        self.world.DestroyBody(self.legs[1])
        self.legs = []

    def _reset(self):
        self._destroy()
        self.world.contactListener_keepref = ContactDetector(self)
        self.world.contactListener = self.world.contactListener_keepref
        self.game_over = False
        self.prev_shaping = None
        self.throttle = 0.0
        self.gimbal = 0.0
        self.fuel = START_FUEL

        # terrain
        # self.terrainheigth = self.np_random.uniform(H / 20, H / 10)
        self.terrainheigth = H / 15
        # barge_pos = self.np_random.uniform(0, BARGE_WIDTH / SCALE) + BARGE_WIDTH / SCALE
        barge_pos = W / 2
        self.helipad_x1 = barge_pos - BARGE_WIDTH / 2
        self.helipad_x2 = self.helipad_x1 + BARGE_WIDTH
        self.helipad_y = self.terrainheigth + BARGE_HEIGHT

        self.water = self.world.CreateStaticBody(
            fixtures=fixtureDef(
                shape=polygonShape(vertices=[(0, 0), (W, 0), (W, self.terrainheigth), (0, self.terrainheigth)]),
                friction=0.1,
                restitution=0.0)
        )
        self.water.color1 = (35. / 255, 70. / 255, 177. / 255)

        self.barge = self.world.CreateStaticBody(
            fixtures=fixtureDef(
                shape=polygonShape(
                    vertices=[(self.helipad_x1, self.terrainheigth),
                              (self.helipad_x2, self.terrainheigth),
                              (self.helipad_x2, self.terrainheigth + BARGE_HEIGHT),
                              (self.helipad_x1, self.terrainheigth + BARGE_HEIGHT)]),
                friction=0.4,
                restitution=0.0)
        )

        self.barge.color1 = (0.2, 0.2, 0.2)

        initial_x = np.random.uniform(W / 3, W * 2 / 3)
        initial_y = H * 0.95

        self.lander = self.world.CreateDynamicBody(
            position=(initial_x, initial_y),
            angle=0.0,
            fixtures=fixtureDef(
                shape=polygonShape(vertices=LANDER_POLY),
                friction=0.5,
                categoryBits=0x0010,
                maskBits=0x001,  # collide only with ground
                restitution=0.0)
        )

        self.lander.mass = 18000
        self.lander.color1 = (0.85, 0.85, 0.85)
        self.lander.linearVelocity = (self.np_random.uniform(-50 * INITIAL_RANDOM, 50 * INITIAL_RANDOM),
                                      self.np_random.uniform(-500 * INITIAL_RANDOM, -400 * INITIAL_RANDOM))
        self.lander.angularVelocity = self.np_random.uniform(-0.05 * INITIAL_RANDOM, 0.05 * INITIAL_RANDOM)

        for i in [-1, +1]:
            leg = self.world.CreateDynamicBody(
                position=(initial_x - i * LEG_AWAY, initial_y + ROCKET_WIDTH * 0.2),
                angle=(i * 0.05),
                fixtures=fixtureDef(
                    shape=polygonShape(vertices=LEG_POLY(i)),
                    density=1.0,
                    restitution=0.0,
                    friction=0.4,
                    categoryBits=0x0020,
                    maskBits=0x001)
            )
            leg.ground_contact = False
            leg.color1 = (0.25, 0.25, 0.25)
            rjd = revoluteJointDef(
                bodyA=self.lander,
                bodyB=leg,
                localAnchorA=(i * LEG_AWAY, ROCKET_WIDTH * 0.2),
                localAnchorB=(0, 0),
                enableMotor=True,
                enableLimit=True,
                maxMotorTorque=LEG_SPRING_TORQUE,
                motorSpeed=-0.15 * i  # low enough not to jump back into the sky
            )
            if i == 1:
                rjd.lowerAngle = +BASE_ANGLE - SPRING_ANGLE  # Yes, the most esoteric numbers here, angles legs have freedom to travel within
                rjd.upperAngle = +BASE_ANGLE
            else:
                rjd.lowerAngle = -BASE_ANGLE
                rjd.upperAngle = -BASE_ANGLE + SPRING_ANGLE
            leg.joint = self.world.CreateJoint(rjd)
            self.legs.append(leg)

        self.drawlist = [self.lander] + self.legs + [self.water] + [self.barge]

        if CONTINUOUS:
            return self._step([0, 0])[0]
        else:
            return self._step(4)[0]

    def _step(self, action):

        if CONTINUOUS:
            if action[0] > 0.2:
                self.gimbal += 0.05
            elif action[0] < -0.2:
                self.gimbal -= 0.05
            if action[1] > 0.2:
                self.gimbal += 0.10
            elif action[1] < -0.2:
                self.gimbal -= 0.10
        else:
            if action == 0:
                self.gimbal += 0.07
            elif action == 1:
                self.gimbal -= 0.07
            elif action == 2:
                self.throttle += 0.12
            elif action == 3:
                self.throttle -= 0.12

        self.gimbal = np.clip(self.gimbal, -GIMBAL_THRESHOLD, GIMBAL_THRESHOLD)
        self.throttle = np.clip(self.throttle, 0.1, 1.0)
        # Engines
        tip = (math.sin(self.lander.angle), math.cos(self.lander.angle))

        ox = tip[0]
        oy = -tip[1]
        impulse_pos = (self.lander.position[0], self.lander.position[1])
        impulse = (-math.sin(self.lander.angle + self.gimbal) * MAIN_ENGINE_POWER * self.throttle,
                   math.cos(self.lander.angle + self.gimbal) * MAIN_ENGINE_POWER * self.throttle)
        self.lander.ApplyForce(impulse, impulse_pos, True)

        self.world.Step(1.0 / FPS, 12, 4)

        pos = self.lander.position
        vel = self.lander.linearVelocity
        state = [
            (pos.x - W / 2),
            (pos.y - self.helipad_y),
            vel.x / FPS,
            vel.y / FPS,
            self.lander.angle,
            20 * self.lander.angularVelocity / FPS,
            1.0 if self.legs[0].ground_contact else 0.0,
            1.0 if self.legs[1].ground_contact else 0.0,
            self.throttle,
            self.gimbal
        ]
        self.fuel -= self.throttle

        distance = np.linalg.norm(state[0:2])
        speed = np.linalg.norm(state[2:4])
        angle = abs(state[4])

        # REWARD -----------------------------------------------
        reward = 0.0
        shaping = - 100 * distance \
                  - 100 * speed \
                  - 100 * angle \
                  + 20 * (state[6] + state[7])

        if self.prev_shaping is not None:
            reward += shaping - self.prev_shaping
        self.prev_shaping = shaping

        done = False
        if self.game_over or abs(state[0]) > W / 2 or pos.y > H or self.fuel < 0:
            done = True
            reward = -300
        if not self.lander.awake:
            done = True
            reward = 300 + self.fuel
        # REWARD -----------------------------------------------

        return np.array(state), reward, done, {}

    def _render(self, mode='human', close=False):
        if close:
            if self.viewer is not None:
                self.viewer.close()
                self.viewer = None
            return

        from gym.envs.classic_control import rendering
        if self.viewer is None:
            self.yellow = (206. / 255, 206. / 255, 2. / 255)
            self.viewer = rendering.Viewer(VIEWPORT_W, VIEWPORT_H)
            self.viewer.set_bounds(0, W, 0, H)

            sky = rendering.FilledPolygon([(0, 0), (0, H), (W, H), (W, 0)])
            sky.set_color(126. / 255, 150. / 255, 255. / 255)
            self.viewer.add_geom(sky)

            self.rockettrans = rendering.Transform()

            # engine
            engine = rendering.FilledPolygon(ENGINE_POLY)
            self.enginetrans = rendering.Transform()
            engine.add_attr(self.enginetrans)
            engine.add_attr(self.rockettrans)
            engine.set_color(.4, .4, .4)
            self.viewer.add_geom(engine)

            # fire
            fire = rendering.FilledPolygon(
                [(ENGINE_WIDTH * 0.4, 0), (-ENGINE_WIDTH * 0.4, 0), (-ENGINE_WIDTH * 0.7, -ENGINE_HEIGHT * 4),
                 (0, -ENGINE_HEIGHT * 6), (ENGINE_WIDTH * 0.7, -ENGINE_HEIGHT * 4)])
            fire.set_color(249. / 255, 241. / 255, 114. / 255)
            self.firescale = rendering.Transform(scale=(1, 1))
            self.firetrans = rendering.Transform(translation=(0, -ENGINE_HEIGHT))
            fire.add_attr(self.firescale)
            fire.add_attr(self.firetrans)
            fire.add_attr(self.enginetrans)
            fire.add_attr(self.rockettrans)
            self.viewer.add_geom(fire)

        for obj in self.drawlist:
            for f in obj.fixtures:
                trans = f.body.transform
                path = [trans * v for v in f.shape.vertices]
                self.viewer.draw_polygon(path, color=obj.color1)
                path.append(path[0])
                if hasattr(obj, 'color2'):
                    self.viewer.draw_polyline(path, color=obj.color2, linewidth=2)

        self.viewer.draw_polyline([(self.helipad_x2, self.terrainheigth + BARGE_HEIGHT),
                                   (self.helipad_x1, self.terrainheigth + BARGE_HEIGHT)], color=self.yellow,
                                  linewidth=2)

        for x in [self.helipad_x1, self.helipad_x2]:
            flagy1 = self.terrainheigth
            flagy2 = flagy1 + BARGE_HEIGHT * 1.5
            self.viewer.draw_polyline([(x, flagy1), (x, flagy2)], color=self.barge.color1, linewidth=2)

        pos = self.lander.position
        self.rockettrans.set_translation(pos[0], pos[1])
        self.rockettrans.set_rotation(self.lander.angle)
        self.enginetrans.set_rotation(self.gimbal)
        self.firescale.set_scale(newx=1, newy=self.throttle * np.random.uniform(0.9, 1.1))

        return self.viewer.render(return_rgb_array=mode == 'rgb_array')