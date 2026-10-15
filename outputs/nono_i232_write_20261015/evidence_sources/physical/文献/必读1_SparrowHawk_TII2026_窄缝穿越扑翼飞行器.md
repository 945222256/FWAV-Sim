# 必读1 | SparrowHawk — A Compact Autonomous Flapping-Wing Aerial Vehicle: Design, Modeling, and Vision-Based Control for Narrow Gap Traversal
**Jizhou Jiang, Wenfu Xu, Erzhen Pan, Haoyu Wang, Zhenkun Gong** | IEEE TII Vol.22 No.2, Feb 2026, pp.1562–1573 | DOI: 10.1109/TII.2025.3628640
> 逐句精读×2（跳过 related work 与视觉实现细节）



---
<!-- 第 1 页 / 共 12 页 -->

1562                                           IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026
A Compact Autonomous Flapping-Wing Aerial
Vehicle: Design, Modeling, and Vision-Based
Control for Narrow Gap Traversal
Jizhou Jiang    , Student Member, IEEE, Wenfu Xu    , Senior Member, IEEE, Erzhen Pan    ,
Haoyu Wang    , Student Member, IEEE, and Zhenkun Gong
Abstract—Aggressive autonomous flight through nar-   for FWAVs. Through multiple repeated trials,  it achieved
row gaps presents a critical challenge in drone racing.  success rates of 80% under normal lighting and 60% un-
As flapping-wing aerial vehicles (FWAVs) evolving toward   der low-light conditions, significantly outperforming a hu-
autonomous navigation, avian-inspired FWAVs are signifi-  man pilot, the conventional unmodified Hough circle detec-
cantly hindered by their single-modal flight dynamics and   tor, and the deep learning–based YOLOv11 algorithm. This
nonlinear underactuated characteristics, leading to the  work advances perception-driven control methodologies
largely unexplored area of narrow gap traversal. To ad-   for FWAVs, paving the way for agile autonomous navigation
dress this gap, this article introduces SparrowHawk, a com-   in confined environments.
pact autonomous flapping-wing system optimized for aero-
dynamic efficiency and structural rigidity. Its design en-     Index  Terms—Autonomous  navigation,  biologically-
ables a favorable payload-to-weight ratio, accommodating   inspired robots, dynamics modeling, flapping-wing aerial
onboard sensing for external-positioning-free localization   vehicle (FWAV), mechanical design, vision-based control.
and maneuverable forward flight. By leveraging the cycle-
averaged method for modeling, the system is simplified to
a nonlinear time-invariant model, with kinematic and dy-
namic equations derived in a unified state-space formula-
I. INTRODUCTIONtion. For robust real-time gate detection during flight, an
adaptive Hough transform-based visual algorithm is pro-      IO-INSPIRED flapping-wing  aerial vehicles (FWAVs)
posed, yielding pixel-space geometric parameters of gaps       have emerged as a promising paradigm in the field of aerialwithout any prior knowledge. The servo control framework B                                                                    robotics. These robots mimic the flight mechanisms of birds
employs a closed-loop dual-loop strategy, integrating a
and insects by producing aerodynamic lift and thrust through
TECS-based altitude controller with a cascaded PID atti-
periodic wing flapping, coupled with passive wing deforma-tude controller to ensure precise position and orientation
control during autonomous gap traversal. Indoor and out-   tion and torsional motion [1]. Benefiting from their unique
door flight experiments demonstrate that SparrowHawk ex-   biomimetic features, such as multimodal transitions [2], agile
ecutes tight-radius banked turns and altitude control with   maneuverability [3], perching and grasping capabilities [4], [5],
a mean error of 0.345 m. The robot successfully traverses  and superior aerodynamic efficiency at low Reynolds numbers
an 80 cm-diameter circular gap at speeds up to 5.6 m/s   [6], [7], FWAVs exhibit considerable potential for applications
with a 60 cm wingspan, signifying the first achievement   in industrial inspection [6], [8], emergency response and search-
and-rescue [7], autonomous operations in confined or complex
industrial spaces [9], [10].
Received 2 September 2025; revised 19 October 2025 and 31 Octo-    With ongoing advancements in unsteady aerodynamics and
ber 2025; accepted 31 October 2025. Date of publication 19 November
overall design, FWAVs have steadily matured and are gen-2025; date of current version 5 February 2026. This work was sup-
ported in part by the National Natural Science Foundation of China   erally categorized into two types: micro-scale insect-inspired
under Grant 62573160 and Grant 62233001, in part by the Shen-   tailless designs, exemplified by the DelFly Explorer & Nim-
zhen Natural Science Foundation in Basic Research Fund under Grant   ble [11], [12], KUBeetle-S [13], [14], NUS-RoboticBird [15],
JCYJ20250604145414020 and Grant JCYJ20241202123724032, and  and medium-to-large-scale avian-inspired platforms, including
in part by Shenzhen Excellent Technology Innovation Talents Training
Program under Grant RCJC20200714114436040. Paper no. TII-25-   Falcon [1], Dove [6], E-FLAP [16] HIT-Hawk & Phoenix [17],
6016. (Corresponding authors: Wenfu Xu; Erzhen Pan.)            USTB-Hawk [18], and Cloud-Owl [19]. Alongside the evolution
Jizhou Jiang, Haoyu Wang, and Zhenkun Gong are with the School   of these platforms, the demand for autonomous navigation in
of Robotics and Advanced Manufacture, Harbin  Institute  of Tech-   constrained environments has increasingly emerged [10], [20].
nology, Shenzhen 518055, China (e-mail: 22b953004@stu.hit.edu.cn;
In nature, birds such as sparrowhawks and falcons showcase ex-heavyrain@stu.hit.edu.cn; 24s153131@stu.hit.edu.cn).
Wenfu Xu and Erzhen Pan are with the School of Robotics and Ad-   traordinary agile maneuvers to navigate through narrow spaces,
vanced Manufacture, Harbin Institute of Technology, Shenzhen 518055,   evade obstacles, and capture prey [1], [21]. However, compared
China, also with the Guangdong Key Laboratory of Intelligent Morphing   with their biological counterparts and other types of MAVs,
Mechanisms and Adaptive Robotics, Shenzhen 518055, China, and   current FWAVs still face significant challenges and unresolved
also with Guangdong Biomimetic Intelligent Unmanned System Engi-
neering Technology Research Center, Shenzhen 518052, China (e-mail:   research gaps in achieving fully autonomous navigation. A
wfxu@hit.edu.cn; perzhen@hit.edu.cn).                                typical application is the gap traversal problem, which demands
Digital Object Identifier 10.1109/TII.2025.3628640                    precise whole-body perception and control when navigating at
1941-0050 © 2025 IEEE. All rights reserved, including rights for text and data mining, and training of artificial intelligence and similar technologies.
Personal use is permitted, but republication/redistribution requires IEEE permission. See https://www.ieee.org/publications/rights/index.html
for more information.
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 2 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1563
high speeds through confined environments. These challenges   for FWAVs, pioneering high-speed narrow obstacle avoidance
can be summarized as follows.                                     capabilities comparable to the natural flight abilities of avian
1) High-payload  flight challenges: The  inherently lim-   species.
ited wing loading capacity and strict power constraints    The rest of this article is organized as follows. Section II
severely restrict the selection of onboard perception sen-   reviews the research progress in MAV aggressive gate traversal
and autonomous perception and avoidance control methods       sors and avionics systems.
for FWAVs. Section III introduces the design and avionics of   2) Onboard perception challenges: Significant periodic os-
the Sparrowhawk platform. Section IV presents the kinematic
cillations and mechanical vibrations induced by the recip-                                                       and dynamic modeling of the Sparrowhawk, along with the
rocating flapping motion introduce additional measure-                                                            corresponding state-space equations. Section V provides a de-
ment noise, which manifests as camera motion blur and   tailed explanation of the visual-guided controller design. In
IMU bias accumulation.                                 Section VI, the real-world flight, the comparison of the gate
3) Perception-driven servo control challenges: The non-   detection algorithm, and narrow-gap traversal experiments are
holonomic underactuation and strong aerodynamic cou-   conducted. Section VII presents the comparison and valida-
pling complicate  precise dynamic modeling. Unlike   tion with other similar FWAV platforms, as well as the static
hover-capable vehicles, which are capable of multiple   flapping endurance tests. Finally, Section VIII concludes this
article.      flight modals, the tailed design of this FWAV restricts it to
high-speed forward flight, posing challenges for precise

## II. RELATED WORKS
whole-body autonomous vision-based control in confined
environments.                                     Autonomous aggressive flight through narrow gaps has been
To  address  the  aforementioned  challenges,  this  article   extensively explored in quadrotors and is regarded as one of the
presents a compact and autonomous avian-inspired FWAV  most challenging problems [22]. This task demands whole-body
namedaftertheSparrowhawk,oneofthesmallest-wingedhawks   perception and control, along with the precise and rapid coor-
in nature. We provide a comprehensive overview of the de-   dination of the vehicle’s position and attitude within a limited
sign, modeling, development, and vision-based control methods.   response time. The traditional decoupled approach follows a
The main contributions of this work can be summarized in   localization-planning-control pipeline.  It leverages nonlinear
three main aspects. First, a novel compact FWAV platform   optimization, employing the differential flatness of polynomials
is developed, integrating aerodynamic layout optimization to  and B-splines to generate dynamically feasible smooth tra-
achieve an exceptional payload ratio exceeding 50% while   jectories, with model predictive control (MPC) or differential
maintaining high maneuverability. The platform incorporates a   flatness control serving as the controllers [21], [23], [24], [25].
lightweight, high-resolution monocular vision system, a multi-   Conversely, an alternative approach utilizes a learning-based
redundant IMU flight controller, and an optical flow module   end-to-end policy that directly generates low-dimensional con-
for autonomous self-localization, with image processing for   trol commands from high-dimensional observations, enabling
remote control performed at the ground station. Furthermore, a   the establishment of a Sim2Real transfer from simulation to
cycle-averaged formulation is employed to systematically derive   the real world [22], [26], [27]. The Swift system [28], [29],
a physically consistent state-space representation, providing a   trained in simulation through deep reinforcement learning, in-
solid foundation for vision-based control. Second, a real-time,   tegrates feedback neural networks into the control policy and
efficient sliding window-based visual gate detection algorithm   uses nonparametric empirical noise models to address Sim2Real
that incorporates an adaptive mechanism and leverages the   transfer discrepancies. This work marks a milestone in which
Hough transform is proposed. The algorithm ensures robust   learning-based methods have surpassed human pilots in drone
and accurate gate recognition under rapid motion and scale   racing.
variations, without requiring any prior knowledge. The servo     Inspired by the aggressive traversal through narrow gaps
controller implemented on PX4 firmware with offboard control   exhibited by quadrotors and drone racing, performing similar
is integrated into a dual-loop framework that employs vision,   tasks with FWAV platforms remains an unexplored research
IMU, and optical flow fusion for 6-DoF state estimation without   area. Early work related to perception-driven avoidance con-
external localization, facilitating agile and reliable gate traver-   trol primarily focused on tailless micro insect-inspired robots.
sal. Finally, extensive experiments were conducted to validate   DelFly Explorer and Nimble pioneer the application of the ap-
the effectiveness of the overall design and the vision-guided   pearance variation cues [30], “Droplet” strategy [12], monocular
controller, including outdoor maneuvering flights, altitude-hold   snake-gate detection [10] and stereo visual semi-global opti-
flights, comparative evaluation of visual detection algorithms   mization LongSeq algorithms [31]. While the Delfly Explorer
on two distinct datasets, and indoor circular gate traversal tests.   demonstrates the ability to explore multiple rooms and traverse
To the best of our knowledge, this is the first practical imple-   gates, it is limited by relatively slow obstacle avoidance flight
mentation of a small-scale flapping-wing robot that integrates   speeds and  its reliance on external motion capture systems
real-time vision-based perception with offboard flight control. It   for localization. Within the domain of medium and large-scale
successfully demonstrates the autonomous traversal of an 80 cm  FWAVs, the FWAF-VID [32], and GRIFFEN datasets [33] rep-
narrow gate by a robot with a 60 cm wingspan, achieving a max-   resent significant advancements in onboard self-localization and
imum speed of 5.6 m/s while maintaining stable forward flight   event-camera robust perception capabilities. Ryu et al. [20] pro-
and exceptional maneuverability. Multiple repeated experiments   pose a vision-based target tracking method that employs color
measuring the success rate under varying illumination con-   segmentation for visual detection, recognizing the color block,
firmed the system’s stability and scalability. This work bridges  and directly converting the error into pitch and yaw voltage
the gap between autonomous perception and offboard control   control commands. However, the direct error mapping without
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 3 页 / 共 12 页 -->

1564                                                     IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026

> **[图]** Fig. 1.  Assembly and modular structural design diagram.
a closed-loop feedback controller cannot guarantee the stability   fuselage-axis-aligned planetary reducer, paired with the brush-
and accuracy of system. Eguiluz et al. [34] propose an onboard   less direct current motor (BLDC) motor, provides the required
event camera-based closed-loop control method that extracts tri-   torque within a compact configuration. The flapping wings are
angular line features for visual servoing, facilitating autonomous   designed based on the high-speed wing morphology of Spar-
target-oriented maneuvering, but without achieving narrow gate  rowhawk, which features greater stiffness on the inner surface
traversal. Learning-based approaches have gradually emerged   for lift production and enhanced flexibility on the outer surface
in FWAV autonomous flight research. Lee et al. [35] support a   for passive deformation. The lift coefficient was determined
learning-based architecture with MPC control for path tracking,  from design parameters, including maximum takeoff weight,
but it lacks real-world experimental validation. Chen et al. [36]   cruising speed, and wing area, with Reynolds numbers ranging
propose a reinforcement learning-based event-triggered planner  from 1.0e5 to 1.5e5. The Eppler 377 airfoil was chosen for the
for autonomous obstacle avoidance. However, the intricate and   outerwingribprofileaftercomparisonswiththeairfoillibrary.In
unsteady aerodynamics of FWAVs continue to pose difficulties   addition, weight reduction measures were implemented for the
in high-fidelity modeling, dynamic parameter identification, and   inner wing rib profile, and holes were added to secure the back
Sim2Real transfer accuracy.                                   support rod. The wings exhibit asymmetric stroke amplitudes,
In response, this article presents a flapping-wing robot plat-   with an upstroke angle of 65° and a downstroke angle of 20°,
form Sparrowhawk with an optimized payload-to-weight ratio   generating rolling moments that enhance lateral agility and roll
for onboard self-localization. A cycle-averaged kinematic and   responsiveness during turns.
dynamic model is derived to support control strategies, while a    The fuselage is designed for the integration of the flapping
real-timevision-basedcirculargatedetectionmethodensuresef-   mechanism, V-tail, flight controller, and video transmission
ficient gate recognition. Finally, a dual-loop closed-loop control   module. To facilitate modular assembly and minimize structural
method is implemented, enabling the first aggressive maneuver   weight, both the main and bottom fuselage components are man-
through a narrow circular gate.                                  ufactured integrally using PLC-based 3-D printing, and further
reinforced with carbon sheets to improve structural stiffness and
strength. The PX4 flight controller and DJI O4 video trans-

## III. DESIGN AND AVIONICS OF SPARROWHAWK
mission unit are mounted using isolating standoffs to mitigate
mechanical vibrations caused by oscillatory motion. The bottle

## A. Mechanical Design
fuselage incorporates a monocular camera and a MicoAir MTF-
The mechanical design of the platform consists of four pri-  01 optical flow and laser sensor. To ensure the camera maintains
mary modules: the flapping mechanism and reducer, flapping   a forward-facing orientation and the optical flow sensor remains
wings, fuselage, and V-tail, as illustrated in Fig. 1. The flapping   in a downward-facing configuration during forward flight, where
mechanism utilizes a compact four-bar linkage, with the rocker   the vehicle typically maintains a pitch angle of approximately
directly functioning as a reciprocating incomplete gear. This   25°, the mounting bracket is deliberately inclined to compensate
integration not only minimizes the number of components but   for this flight attitude. To enhance the bio-inspired fidelity of the
also facilitates symmetric wing actuation by mechanically driv-   platform and mitigate the inherent maneuverability limitations
ing the opposing gear without the need for additional linkages. A  due to the absence of conventional ailerons on wings, a V-tail
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 4 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1565

> **[表]** TABLE I
MAIN SPECIFICATION

> **[图]** Fig. 3.  Layout of the avionics system and onboard sensors.
on an ESP8266 MCU, which establishes a low-cost UDP 14 550
broadcast link between the flight controller and a ground-based
Intel NUC computer, enabling bi-directional transmission of
control commands. The Ground Control Station is composed

> **[图]** Fig. 2.  Mass distribution of sparrowhawk components and sensors.     of a DJI integrated FPV Goggles unit and an NUC11 computer
equipped with a 4-core Intel i5-1135G7 CPU running Ubuntu
20.04. The onboard DJI O4 air unit transmits real-time video
to the FPV goggles through the DJI OcuSync protocol. Theconfiguration is employed for the tail assembly. The V-tail is
goggles are connected to the DJI Fly application via a USBactuated by two independent servos that differentially control
interface, and the video stream is wirelessly mirrored to the NUCaerodynamic flaps to generate asymmetric lift for roll control,
over Wi-Fi, serving as the video input source for the system.while symmetric deflection produces pitch moments for attitude
The video transmission latency at the ground station comprisesregulation. The V-tail is configured with an inclination angle
the transmission delay, DJI Fly screen casting latency, and theof 65°, which facilitates the generation of roll moments during
frame acquisition delay from the ffmpeg-based stream input.turning maneuvers. To prevent potential dynamic instability,
Measurements indicate a stable end-to-end latency of approx-such as spin entry induced by fully actuated control surfaces
imately 480 ms, which is compensated for in the subsequentunder aggressive flight conditions, and to enhance longitudinal
visual guidance controller. In addition, the perception modulestatic stability, fixed aerodynamic surfaces made of KT board
is equipped with a downward-facing optical flow sensor, theare mounted beneath the control flaps. The main specification
MTF-01, which integrates the PMW3901 optical flow sensorof the platform are shown in Table I. The mass distribution of
and the VL53L1X laser sensor. The configuration of the avionicsSparrowhawk is depicted in Fig. 2.
system and onboard sensors is illustrated in Fig. 3.
This configuration enables accurate altitude estimation and

## B. Avionics and Sensors Integration
supports the vehicle’s motion state localization by calculating
Due to the strict constraints on payload and power consump-   optical flow from consecutive frames. The actuator comprises
tion of Sparrowhawk, it is impractical to equip it with a minimal  two servos and a BLDC motor, which is driven via a bidirec-
onboard computer running a Linux system for real-time image   tional DShot electronic speed controller (ESC). All actuators are
processing. Consequently, to ensure stable flight performance,   controlled through PWM signals. To minimize overall weight,
the avionics and sensor integration utilize a ground-based pro-    all avionics components have undergone decasing and weight
cessing scheme, in which both video and data streams are   reduction modifications.
transmitted back to the ground station. The onboard electronic
system is powered by a 2S 300-mAh LiPo battery connected to

## IV. MATH MODELING
the flight controller via a DC–DC converter with an ammeter.
The flight controller utilizes the CUAV Nano V5, which is    The kinematic and dynamic modeling of FWAVs is inherently
flashed with PX4 firmware version 1.16.0. It is powered by  complex due to the aerodynamic coupling and the nonlinear,
an STM32F765 MCU and integrates a triple-redundant IMU,   time-varying characteristics inherent in the system. Given these
an electronic compass, and a barometer. This configuration   challenges, we employed the cycle averaging method to sim-
enablesaccurateattitudeestimationandprovidesreliablecontrol   plify the model of the Sparrowhawk, deriving the unified state-
signal output. An RC receiver communicates with the controller   spacerepresentationofitscorrespondingkinematicanddynamic
through the s.Bus protocol. The Wi-Fi telemetry module is based   equations.
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 5 页 / 共 12 页 -->

1566                                                     IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026
⎡ ⎛              ⎞      ⎤
Jxzpq + (Jy −Jz) qr                l
˙ω(t) = J−1 ⎣ ⎝ Jxz  r2 −p2 + (Jz −Jx) pr ⎠+ m ⎦
(Jx −Jy) pq −Jxzqr        n
(7)
where J is the moment of inertia matrix. The diagonal elements
of this matrix Jx,y,z represent the moments of inertia, while
the off-diagonal Jxz elements represent the products of inertia.
Consideringthat Sparrowhawkis symmetricabout the XZ-plane,
the moment of inertia matrix can be formulated as
Jx    0  −Jxz
J Δ=   0    Jy    0      .               (8)
−Jxz  0    Jz
The inverse of J can be calculated by
⎡               ⎤
JzΓ−1    0   JxzΓ−1                                                                        adj(J)
= ⎣   0     Jy −1    0  ⎦      (9)Fig. 4.  Schematic of the coordinate frame and the force diagram.           J−1 =                                                                        det(J)                                                                            JxzΓ−1   0    JxΓ−1
where Γ Δ= JxJz −J2xz. Hence, the state variables are denoted
The coordinate transformation involves four main frames: the
as X(t) = [r(t), η(t), v(t), ω(t)]T. Therefore, the kinematicworld frame FW, the body frame FB, the camera frame FC,
and dynamic state-space equations can be integrated into aand the pixel frame FP, as shown in Fig. 4. The IMU frame is
unified matrix formassumed to coincide with the body frame.
⎡   ⎤  ⎡          ⎤  ⎡    ⎤
The position and attitude defined in the ENU world frame FW                  ˙r(t)   RWB (η(t)) v(t)        0
can be represented as                                                                ˙η(t)     T (η(t)) ω(t)         0                                                    X(t)˙  = ⎢⎣   ⎥⎦= ⎢⎣            ⎥⎦+ ⎢⎣        ⎥⎦.  (10)                                                                                          ˙v(t)         v(t) × ω(t)     F m−1
r(t) = [x(t), y(t), z(t)]T , η(t) = [φ(t), θ(t), ψ(t)]T .    (1)                ˙ω(t)     J−1ω(t)∧Jω(t)    J−1M
The translational and rotational velocities defined in the body    The ω(t)∧represents a Skey-symmetric matrix, which can be
frame FB can be defined as                                    formally defined as
v(t) = [u(t), v(t), w(t)]T , ω(t) = [p(t), q(t), r(t)]T .   (2)                          0  −r   q
ω(t)∧=   r   0  −p   .             (11)
Considering the coordinate transformation from the body                     −q   p   0
frame to the world frame, the kinematic equation can be ex-
The flapping motion of the wings is a periodic oscillationpressed in the following form:
influenced by time-varying aerodynamic loads, which renders
˙r(t) = RWB (η(t)) v(t)                      the flapping-wing dynamics a nonlinear time-periodic (NLTP)
system. By applying cycle averaging theory and performing
˙η(t) = T (η(t)) ω(t).                   (3)   linearization, the system can be approximated as a linear time-
invariant (LTI) system for analysis [12]. To analyze the afore-
The rotation matrix RWB ∈SO(3) and the Euler angle rate   mentioned aerodynamic forces F and moments M terms, which
transformation matrix T ∈SO(3) can be computed as follows,   can be decomposed into lift, drag, thrust, inertial forces, and
where cφ Δ= cos φ and sφ Δ= sin φ                              corresponding moments. In the flapping motion, only the down-
stroke generates the required positive lift. The total lift is defined
cψcθ  −cφsψ + sφsθcψ   sφsψ + cφsθcψ      as the vertical component of the instantaneous lift produced by
RWB (η(t))= sψcθ   cφcψ + sφsθsψ  −sφcψ + cφsθsψ     both the left and right wings. The dihedral angle is defined as
−sθ        cθsφ              cθcφ           β. The instantaneous lift can be determined by the following
(4)   equation:
1   sφtθ    cφtθ                                             L(t) = 1                                                                                                                       2 cos(β)            (12) T (η(t)) =  0    cφ   −sφ    .                          (5)                     2ρCL(t)SV
0  sφ/cθ  cφ/cθ                                                      where CL(t) is the lift coefficient, which increases with the
angle of attack α below the critical angle, and S is the wing  The forces and moments along the three axes can be sim-
reference area. According to the first mean value theorem forply expressed as F = [fx, fy, fz]T and M = [l, m, n]T . The                                                                        integrals, there exists a dihedral angle within the given flapping
dynamic equations can be derived through the Newton-Euler                                                           amplitude range −20◦< β∗< 65◦, the cycle-averaged form can
formula                                                        be expressed as [37]
rv −qw         fx                                                         T                     1                                                         cos β∗  ˙v(t) =  pw −ru +      fy                            (6)                          ¯L =            L(t)dt.               (13)             m         qu −pv          fz                                         T     0
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 6 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1567
Similarly, the cycle-averaged drag can be derived. The drag   Algorithm 1: GateDetectionWithEvaluation.
coefficient includes both zero-lift drag and induced drag terms,
and can be expressed as
k∗    T
CD = CD0 + CDi = CD0 +        C2L(t)dt     (14)
πAT   0
T
¯D = 1     1            2dt.                       (15)        T   0  2ρCD(t)SV
The thrust is generated by the passive deformation of the
flexible wing during flapping, resembling the fore–aft flapping
motion. The ¯T is the cycle-averaged thrust, which is responsible
forcounteractingthedrag. Thesubscriptv denotestheforcesand
moments generated by the V-wing. The cycle-averaged moment
can be expressed in a generalized form as
T
1
M =        r(t) × F (t)dt.            (16)
T   0
Therefore, the forces and moments generated by the flapping
wings and the V-tail can be formulated as
⎡            ⎤     ⎡           ⎤
¯T + ¯Tv −¯D −¯Dv              Mroll¯  + Mroll,v¯
F = ⎣       0       ⎦, M = ⎣ Mpitch¯  + Mpitch,v¯     ⎦.
¯L + ¯Lv −mg                 Myaw,v¯
(17)

## V. VISION-GUIDED CONTROLLER DESIGN
param1 = 30, param2 = 55. The Evaluator class E assesses the
geometric consistency between detected circles and the regionA. Gates Detection Algorithm
of interest (ROI) in the target image. Specifically, each candidate
In narrow gap detection at the visual front-end, accurately   circle is drawn on a zero-valued background and bitwise ANDed
identifying circular gates during high-speed maneuvers with   with the binary mask of the ROI. The number of overlapping
periodic oscillations poses significant challenges. The algorithm   pixels is normalized by the perimeter of the candidate circle to
should be highly efficient and real-time capable, given the ex-   obtain a matching score, where a higher score indicates better
tremely limited response time for narrow gap traversal under   spatial correspondence with the ROI. Only candidates satisfying
aggressive flight conditions. First, in the absence of any prior   the fit degree thresholds fitDegree = 0.8, minFitDegree = 0.5
knowledge of the gaps, such as QR codes landmarks [28] or  and fall within the top matches maxFitNum = 3 are retained for
pretrained models [22], [26], the gate detection algorithm must   further processing. Given that the accumulator threshold param2
function without external localization assistance. Second, as the   regulates detection strictness and exhibits scale sensitivity, the
perceived radius of the gate varies markedly with distance and   param2_update() dynamically adjusts this threshold in propor-
perspective, the detection process should be adaptive to scale   tion to the detected radius, thereby enhancing robustness under
changes. To meet these requirements, we propose an efficient   high-speed flight conditions. However, per-frame evaluation is
real-time gate detection framework based on the Hough Circle   computationally expensive and incurs high memory overhead.
Transform, enhanced with a confidence evaluation module that  To ensure real-time performance and achieve low-latency frame
selects the most geometrically consistent candidates. To ensure   processing, a sliding window mechanism is employed. Each
temporal efficiency and low memory consumption while im-   frame is selected at every interval of period = 3 frames and the
proving robustness against false detections and outliers, we ap-   evaluated results are stored in a buffer with a size capacity = 7,
ply a frame buffer combined with a sliding window mechanism  where older candidates are automatically discarded once the
to stabilize the estimates of the gate’s center and radius before   buffer limit is exceeded. If valid circular candidates are identified
forwarding to the back-end controller.                             in the current frame and the fitting degree satisfies the predefined
The pseudocode of the visual front-end detection pipeline   threshold, the corresponding center and radius are adopted as
is presented in Algorithm 1. The framework begins by ini-   the final detection result. If no candidates are available, the
tializing the ShapeManager S and FrameButler B, which are   parameter param2 is reinitialized. In all other cases, the detection
responsible for managing shape constraints and video frame   result from the previous frame is retained to ensure temporal
buffering, respectively. For each incoming frame from the input    stability.
video stream V, the pipeline verifies whether the current target
is a circular gate and invokes the comparison method to eval-

## B. Controller Design
uate detection accuracy. Subsequently, each frame undergoes
a sequence of preprocessing steps, including downsampling,    The framework pipeline and control block diagram of the
grayscale, Gaussian filtering, morphological operations, and   vision-based gap traversal control system is illustrated in Fig. 5,
Canny edge detection. Circle candidates are extracted using   including the perception, position, and control modules. In
the Hough Circle Transform with the following parameters   the perception module, front-view images with a resolution
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 7 页 / 共 12 页 -->

1568                                                     IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026

> **[图]** Fig. 5.  Framework pipeline and control block diagram of the vision-based gap traversal controller.
of 640 × 480 captured by the onboard DJI O4 camera are   attributable to the data link transmission and reception. The Δtv
compressed from 1920 × 1080 60 Hz input via the ffmpeg tool.    is determined by measuring the timestamp difference between
These images are then processed using Algorithm 1 to extract   the original video stream, while the Δtc is continuously acquired
the geometric features of the circular gate, where (upm, vpm)  from the MAVROS topic. To address this issue, we employ a
denotes the center and rpm represents the radius of the detected   feedforward compensation strategy based on the pinhole camera
circle. The principal point in the image pixel coordinate is  model and the kinematic model derived above to predict the
defined as (upc, vpc). The position module estimates the 6-DoF   normalized pixel error after Δt = Δtv + Δtc, before entering
state of the robot by fusing optical flow-based visual odometry   the cascaded PID controller, thereby mitigating the effects of the
with IMU measurements via an Extended Kalman Filter (EKF).   delay. The normalized pixel error after feedforward compensa-
Optical flow provides relative position measurement through   tion can be expressed as follows:
pixel displacement across image frames, while altitude is ob-
tained from laser ranging. This enables the system to transition     ˜eu,v(t + Δt) ≈˜eu,v(t) −Jπ(P c(t))ωc(t)∧P c(t)Δt  (19)
into offboard control mode for further guidance. To achieve
gate traversal control, the objective is to align the detected                                            Jπ represents the projection Jacobian matrix, which maps the
gate center with the principal point, with the desired distance                                                                  velocity vector in the body frame to the velocity on the pixel
approaching zero. Consequently, the normalized pixel coordi-                                                                  plane. ωc(t)∧is the skew-symmetric matrix of angular velocity,
nate error and normalized distance error, which is adjusted to                                                       and Pc(t) is the position of the target in the camera frame. Jπ
eliminate the influence of image resolution, can be expressed as                                                         can be derived from the camera model Z(t)p(t) = KP c(t) as
follows:                                                                 follows:
πrp2m       upm −upc                     vpm −vpc                                                                  fxZ−1    0   −fxXcZ−2   ˜eu =                          , ˜ev =                                                 , ˜ed = 1 −                                                         (18)      Jp(P c(t)) =                                                                                                                                                                           .     (20)     W       H        W × H                           0    fyZ−1  −fyYcZ−2
where W and H represent the width and height of the image.
For vision-guided servo control based on ground station remote    As revealed by the state-space equations, pixel coordinate er-
feedback, the total time delay primarily consists of two compo-   rorscannotbedirectlymappedtoeffectivecontrolinputs.Hence,
nents: the fixed inherent end-to-end vision transmission delay   the normalized roll and pitch angle errors are calculated from the
Δtv = 480 ms, and the dynamic Round Trip Time delay, Δtc,   aforementioned error transformation to serve as effective inputs
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 8 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1569

> **[图]** Fig. 6.  Snapshots illustration of outdoor flight experiments. (a) Aggres-
sive turning maneuver. (b) Autonomous flight with altitude holding.

> **[表]** TABLE II                                     Fig. 7.   Altitude variation over time during altitude-hold flight.
COMPARATIVE RESULTS OF VISION-BASED DETECTION ALGORITHMS
the TECS altitude-hold controller utilizing optical flow module
ranging as feedback, the robot was commanded to switch to
Altitude flight mode. Fig. 7 illustrates the altitude variation over
a selected segment of the altitude-hold flight. The target altitude
was set to 4.5 m. The raw altitude was obtained by the flight
controller barometer, while the fused altitude was derived using
an EKF-based estimator integrating both barometric and optical
flow ranging measurements. With a tolerance band of ±0.5 m
defined, 81.4% of the fused altitude measurements fell within
this range, exhibiting a standard deviation of 0.165 m and a mean
deviation of 0.345 m.for the attitude control
˜eu                     ˜ev
˜eφ = arctan     , ˜eθ = arctan     .          (21)   B. Gate Detection Algorithm Validation and Comparison
˜ed                    ˜ed
Prior to the gate traversal experiments, we validated the effec-
The attitude control framework adopts a cascaded control
tiveness and advancement of the proposed visual gate detection
architecture, where an outer-loop controller governs the atti-
algorithm using a real-world circular gate video dataset and by
tude angles and an inner-loop controller regulates the angular
comparison with two benchmark methods. Video datasets were
rates. Specifically, the outer-loop PID controller computes the
recorded in both cluttered and clean dynamic environments, cap-
desired angular velocity commands based on roll and pitch
turing the gate traversal process from distant to close-up perspec-
errors derived from (21). These commands are compared with
tives. A comparative study was subsequently conducted against
the real-time angular velocity measurements from the IMU
the unmodified Hough circle detection algorithm, serving as
to generate rate errors, which are subsequently processed by
the traditional baseline, and the deep learning–based YOLOv11
the inner-loop PID controller to output the required control
algorithm, representing the state-of-the-art baseline. The un-
torques. The longitudinal position control adopts a hierarchical
modified Hough circle detector was applied with downsampling,
TECS-based architecture, where thrust regulates total mechan-
filtering, and edge detection, employing the same parameters
ical energy and pitch angle modulates the energy distribution
as our improved method. The YOLOv11 was trained on a self-
between kinetic and potential components. Altitude and velocity
constructeddatasetof200labeledcircular-gateimages,captured
errors are transformed into desired energy rate commands, with
from multiple viewpoints. To obtain the optimal model, training
feedback from the optical flow sensor and laser rangefinder. The
was performed using only a single class corresponding to the
resulting pitch and thrust, and required control torque commands
circular gate. For a fair comparison, all algorithms were executed
are forwarded to the control allocator and ultimately translated
under identical conditions on the NUC 11 computer used in our
into actuator signals via PWM.
experiments, without GPU acceleration. The temporal results
obtained from testing on the two datasets are shown in Fig. 8.

## VI. EXPERIMENTS
Quantitativeanalysismetricswereemployedtoevaluateboththe
To validate the aggressive maneuvering and altitude-holding   accuracy and efficiency of the detection algorithms. We define
capabilities of the Sparrowhawk, we conducted real-world flight   the circle center position error (CPE) jitter root mean square
and altitude-hold control experiments in an outdoor playground.   error (RMSE) to evaluate detection precision. CPE is defined as
Furthermore, to verify the effectiveness of the proposed vision-   the instantaneous L2 norm between the estimated circle center
based guidance and control method, we carried out gap traversal  and the ground truth, as expressed by CPEt = || ˆCt −CGT ||2.
real-world experiments in a confined indoor environment of  The RMSE of the CPE sequence is subsequently employed to
approximately 20 m × 8 m.                                measure the detection’s overall stability and jitter across the
entire dataset

## A. Outdoor Maneuvering and Altitude-Holding Flight
1   N
CPE RMSE =          CPE2t.          (22)
To examine the practical performance of the proposed prelim-                  N    t=1
inary overall design, we carried out low-altitude outdoor flight
tests, as shown in Fig. 6. During aggressive turning maneuvers,     Furthermore, average frame rate (FPS) and total processing
the robot demonstrated agile fight performance with a turning   time were utilized to assess the detection speed, and the require-
radius below 1 m. Furthermore, to validate the effectiveness of  ment for pre-training was used as an indicator of application
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 9 页 / 共 12 页 -->

1570                                                     IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026

> **[图]** Fig. 8.  Sequence comparison results of the three circular gate detection results on two datasets. Columns represent sequential video frames over
time, and rows correspond to the original frame, proposed method, original Hough transform, and YOLOv11 algorithm results.
complexity. These quantitative metrics for the algorithms are
summarized in Table II. Experimental results show that the
proposed detection algorithm achieves superior performance in
terms of average frame rate, detection robustness, and recogni-
tion accuracy under both cluttered and dynamic environments.
In the cluttered environment, the conventional Hough detec-
tor produced multiple false positives, and YOLOv11 exhibited
similar shortcomings. Moreover, YOLOv11 failed to accurately
estimate the geometric center of the gate when only partial
circular features were present, and without GPU acceleration    Fig. 9.   (a) Sparrowhawk dimensions. (b) Circular gate dimensions.
achieved an average frame rate of only about 10 fps. In the clean    (c) Geometric size comparison between the Sparrowhawk and the gate.
dynamic dataset, all three algorithms initially provided relatively
stable detections. However, as the gate’s pixel size increased, the
conventional Hough detector and YOLOv11 failed to accurately   the TECS-based position loop will be transmitted to the PX4
and consistently track and recognize the circular gate.            control allocator through MAVROS. The allocator generates
DShot commands for the ESCs to maintain altitude and PWM
signalsfortheservostocontrolattitude.Aftertakeoff,thevision-

## C. Vision-Guided Indoor Narrow Gap Traversal
guided controller effectively aligns the camera’s principal point
Drawing inspiration from the agile maneuverability and ob-   with the center of the circular gate, guiding the robot to traverse
stacle avoidance capabilities exhibited by sparrowhawks navi-   through the gate. We successfully performed gate traversal under
gating through constrained environments, we conducted multi-   varying lighting conditions and carried out repeated experiments
ple real-world indoor experiments involving narrow-gap traver-   to enable comparative analysis. Fig. 10 shows frontal and side
salundervaryinglightingconditionstoevaluatetheperformance   views of the robot successfully traversing the circular gate
of the proposed system. The diameter of the circular gate was set   under both daytime and low-light conditions, along with the
to 80 cm, while the Sparrowhawk’s wingspan is 60 cm, leaving   corresponding FPV sequence. Flight trajectory snapshots under
only 10 cm of horizontal and vertical safety clearance, requiring   daytime normal illumination and nighttime low-light conditions
precise whole-body control to flight through the center of the   are shown in Fig. 10(a) and (c). The corresponding FPV camera
gate. The dimensional comparison between the Sparrowhawk   images and the edge-detection outputs of the gate detection
and the circular gate is illustrated in Fig. 9.                     algorithm are displayed in the first and second rows of Fig. 10(b)
The gate traversal experiments are conducted as follows. After   and (d), respectively. To make a comprehensively evaluate
being hand-launched, the robot autonomously performs gate   the generalizability and scalability of the narrow gap traversal
detection, levels its flight under servo control, and flies through   task, multiple groups of gate traversal success rate experiments
the circular gate. Localization data is acquired using an onboard  were conducted under varying lighting conditions. For baseline
optical flow module, which allows the robot to autonomously   comparison, a professional human pilot, the unmodified Hough
switch to offboard control mode upon detecting the circular   circle algorithm, and the YOLOv11 detector, both of which
gate. Angular velocity commands, derived from a cascaded  employ the same cascaded PID control scheme as our proposed
attitude-loop PID controller, and thrust commands generated by   system, were subjected to the same repeated experiments under
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 10 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1571

> **[图]** Fig. 10.  Snapshot sequence of Sparrowhawk performing narrow circular gate traversal under varying illumination conditions. (a) Agile narrow-gap
gate traversal flight trajectory under normal illumination. (b) Sequence diagram of gap traversal from FPV perspective under normal illumination.
(c) Agile narrow-gap gate traversal flight trajectory under low-light conditions. (d) Sequence diagram of gap traversal from FPV perspective under
low-light conditions.

> **[图]** Fig. 11.  Comparison of gate-traversal success rates across different
lighting conditions and frameworks.
identical conditions. Based on repeated flight tests, with at least
five trials per set, the proposed system achieved an 80% success
rate under normal lighting and a 60% success rate in low-light
conditions. Although low-light conditions slightly degrade the
performance of the vision-based front-end, the system maintains
a robust success rate. In comparison, the human pilot achieved a
10% success rate under normal lighting and failed entirely under
low-light conditions. The low-light scenario posed a significant
challenge for the human pilot, who struggled to complete the
complex maneuver sequence within the limited reaction time,
using only a third-person perspective. Meanwhile, frameworks
relying on the unmodified Hough circle detector and YOLOv11
failed to provide accurate gate-center geometry, leading to erro-    Fig. 12.  Temporal evolution of Euler angles, angular velocities, and
three-dimensional resultant translational velocities.
neous visual-servocontrol. Thesuccess rateresults arepresented
in Fig. 11.
To illustrate the traversal process in detail, representative data   angular velocities are 3.508 rad/s and 12.949 rad/s, showing
from successful gate traversal experiments under normal illumi-   a strong correlation with the variations in the normalized roll
nation conditions are presented. The quantitative results of these  and pitch angular velocity setpoints generated by the cascaded
maneuvers are shown in Figs. 12 and 13. The traversal maneuver  PID controller. This corelation is also reflected in the sudden
flight process was completed within 1 s. During the traversal, the   transients in the pitch/roll angles and angular velocities. Specif-
oscillation amplitudes of the roll and pitch angles are 1.368 rad   ically, after the robot levels out from takeoff, the visual servo
and 1.097 rad, respectively, with maximum deviations of 2.251   controller outputs pitch/roll angular velocity setpoints based on
rad and 2.176 rad. The oscillation amplitudes of the roll and pitch   the position of the principal point relative to the identified gate
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 11 页 / 共 12 页 -->

1572                                                     IEEE TRANSACTIONS ON INDUSTRIAL INFORMATICS, VOL. 22, NO. 2, FEBRUARY 2026

> **[表]** TABLE III
COMPARATIVE ANALYSIS OF VALIDATION RESULTS WITH OTHER FLAPPING-WING PLATFORMS

> **[图]** Fig. 14.   (a) Setup of the static flapping prototype. (b) Sideview of the
static flapping experiment. (c) Snapshot of the downstroke motion.

> **[图]** Fig. 13.  Temporal evolution of normalized pixel errors in u and v axes,   previously derived state-space model, system identification is
and control setpoint of roll and yaw angular rates.                   performed using experimental data from gap traversal flight
tests. The eigenvalues of the linearized model, together with the
center. This manifests as an increase in pitch for rapid climbing  computed natural frequencies and damping ratios, confirm that
to the gate’s height, and a large roll maneuver to execute the   the system exhibits lateral dynamic stability. In addition, flight
necessaryleftturnthroughthecenter.Thepeakresultantvelocity   performance parameters are presented for comparative analysis.
during gate traversal reached 5.6 m/s, while the normalized
mean absolute tracking errors along the u and v axis in the   B. Static Flapping Experiments and Durability Validation
pixel coordinate system are 4.21% and 6.15%, respectively.
To evaluate the durability and reliability of the Sparrowhawk,The satisfactory results of the gate traversal control validate the
we conducted continuous static flapping experiments to assesseffectiveness of the proposed vision-based guidance and control
the durability of the transmission system, wings, and airframemethod.
mechanical structure, as illustrated in Fig. 14. The prototype
was mounted on a torsional spring support bracket that allows

## VII. DISCUSSION
a single degree of freedom motion along the z-axis, and was
operated continuously for more than one hour at throttle levelsA. Comparative Validation With Other Platforms
exceeding 50%. Results from both indoor/outdoor flight tests
To provide further validation for our proposed design and  and static flapping experiments demonstrate that the transmis-
model, we have conducted a detailed numerical comparison with   sion system and wings of the prototype can withstand over 100
state-of-the-art flapping-wing platforms reported in the related  h of continuous flapping. Moreover, the prototype can sustain
literature, examining their trim values, eigenvalues of the lin-   repeatable performance for over 50 consecutive flights without
earized model, and flight performance parameters. The detailed   severe landing impacts.
comparison results are presented in Table III. Specifically, trim
valuesrepresentthesteady-stateequilibriumoftheaircraftunder

## VIII. CONCLUSION
a specific flight condition. For our validation, a trim state is
defined by the flapping frequency, flapping amplitude, and angle     Emulating avian obstacle avoidance maneuvers could signifi-
of attack required to maintain a steady flight speed of 6 m/s. In   cantly enhance the survival, operational safety, and environmen-
Section IV, the nonlinear and quasi-steady aerodynamic NLTP    tal adaptability of FWAVs in industrial scenarios. Achieving
system at low Reynolds numbers is reformulated as an LTI sys-   fully autonomous, safety-assured navigation necessitates im-
temthroughcycle-averagingandlinearization,enablingstability   provements in flight performance, state estimation accuracy, and
assessment via the eigenvalues of the system matrix [39], [40].   precise perception-driven control. To address the challenges of
For comparison, the lateral dynamics identification parameters   high-load flight, onboard sensing, and servo control encountered
reported in [1] are adopted as a benchmark. Based on periodic  by FWAVs, this article introduces Sparrowhawk, detailing its
averaging and small-disturbance theories [41], along with the   mechanical design, sensor integration, modeling, and proposes
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.


---
<!-- 第 12 页 / 共 12 页 -->

JIANG et al.: COMPACT AUTONOMOUS FWAV: DESIGN, MODELING, AND VISION-BASED CONTROL FOR NARROW GAP TRAVERSAL                1573
a vision-guided real-time closed-loop control framework. The    [18] X. Wu, W. He, Q. Wang, T. Meng, X. He, and Q. Fu, “A long-endurance
superiority of the proposed vision-based detection algorithm is        flapping-wing robot based on mass distribution and energy consume
validated through benchmark comparison experiments. Indoor        method,” IEEE Trans. Ind. Electron., vol. 70, no. 8, pp. 8215–8224,
Aug. 2023.
and outdoor flight tests demonstrate that the system can per-    [19] R. Meng, B. Song, J. Xuan, and X. Yang, “Design and verification of a
form tight-radius banked turns, altitude-holding flight, and au-         large-scaled flapping-wing aircraft named ‘cloud Owl’,” Appl. Sci., vol. 13,
tonomous circular gap traversal with only 10 cm safety margins         no. 9, May 2023, Art. no. 5667.
at a flight speed of 5.6 m/s. In addition, multiple repeated exper-    [20]  S. Ryu, U. Kwon, and H. J. Kim, “Autonomous flight and vision-based
target tracking for a flapping-wing MAV,” in Proc. IEEE/RSJ Int. Conf.
iments under varying lighting conditions confirm the robustness                                                                                                         Intell. Robots Syst., Oct. 2016, pp. 5645–5650.
and scalability of the proposed system. Future work will focus on    [21] M. Wang et  al., “Unlocking aerobatic potential of quadcopters: Au-
enhancing the robot’s onboard computing capabilities and train-        tonomous freestyle flight generation and execution,” Sci. Robot., vol. 10,
ing more accurate dynamic models via learning-based methods        2025, Art. no. eadp9905.
to support the deployment of advanced control strategies.           [22] Y. Xie, M. Lu, R. Peng, and P. Lu, “Learning agile flights through narrow
gaps with varying angles using onboard sensing,” IEEE Robot. Autom.
Lett., vol. 8, no. 9, pp. 5424–5431, Sep. 2023.
[23] B. Penin, P. R. Giordano, and F. Chaumette, “Vision-based reactive plan-
REFERENCES                                    ning for aggressive target tracking while avoiding collisions and occlu-
sions,” IEEE Robot. Autom. Lett., vol. 3, no. 4, pp. 3725–3732, Oct. 2018.
[1] A. Chen, B. Song, Z. Wang, D. Xue, and K. Liu, “A novel actuation strategy    [24] Y. Ren et al., “Safety-assured high-speed navigation for MAVs,” Sci.
for an agile bioinspired FWAV performing a morphing-coupled wingbeat         Robot., vol. 10, 2025, Art. no. eado6187.
pattern,” IEEE Trans. Robot., vol. 39, no. 1, pp. 452–469, Feb. 2023.        [25]  J. Tordesillas, B. T. Lopez, M. Everett, and J. P. How, “FASTER: Fast and
[2] C. Wu, Y. Xiao, J. Zhao, J. Mou, F. Cui, and W. Liu, “A multi-modal tailless         safe trajectory planner for navigation in unknown environments,” IEEE
flapping-wing robot capable of flying, crawling, self-righting and hori-         Trans. Robot., vol. 38, no. 2, pp. 922–938, Apr. 2022.
zontal take-off,” IEEE Robot. Autom. Lett., vol. 9, no. 5, pp. 4734–4741,    [26]  J. Lin, L. Wang, F. Gao, S. Shen, and F. Zhang, “Flying through a
May 2024.                                                             narrow gap using neural network: An end-to-end planning and control
[3]  S. Kim, Y.-H. Hsiao, Z. Ren, J. Huang, and Y. Chen, “Acrobatics at the         approach,” in Proc. IEEE/RSJ Int. Conf. Intell. Robots Syst., Nov. 2019,
insect scale: A durable, precise, and agile micro–aerial robot,” Sci. Robot.,         pp. 3526–3533.
vol. 10, no. 98, Jan. 2025, Art. no. eadp4256.                              [27]  S. Chen, Y. Li, Y. Lou, K. Lin, and X. Wu, “Learning real-time dy-
[4] R. Zufferey, J. Tormo-Barbero, D. Feliu-Talegón, S. R. Nekoo, J. Á.        namic responsive gap-traversing policy for quadrotors with safety-aware
Acosta, and A. Ollero, “How ornithopters can perch autonomously on          exploration,” IEEE Trans.  Intell. Veh., vol. 8, no. 3, pp. 2271–2284,
a branch,” Nature Commun., vol. 13, no. 1, Dec. 2022, Art. no. 7713.            Mar. 2023.
[5]  F. J. Maldonado, J. A. Acosta, J. Tormo-Barbero, P. Grau, M. M. Guzman,    [28] E. Kaufmann, L. Bauersfeld, A. Loquercio, M. Müller, V. Koltun, and
and A. Ollero, “Adaptive nonlinear control for perching of a bioinspired        D. Scaramuzza, “Champion-level drone racing using deep reinforcement
ornithopter,” in Proc. IEEE/RSJ Int. Conf. Intell. Robots Syst., Oct. 2020,          learning,” Nature, vol. 620, no. 7976, pp. 982–987, Aug. 2023.
pp. 1385–1390.                                                           [29] Y. Song, A. Romero, M. Müller, V. Koltun, and D. Scaramuzza, “Reaching
[6] W.Yang,L.Wang,andB.Song,“Dove:Abiomimeticflapping-wingmicro         the limit in autonomous racing: Optimal control versus reinforcement
air vehicle,” Int. J. Micro Air Veh., vol. 10, no. 1, pp. 70–84, Mar. 2018.           learning,” Sci. Robot., vol. 8, no. 82, Sep. 2023, Art. no. eadg1462.
[7] D. Gayango, R. Salmoral, H. Romero, J. M. Carmona, A. Suarez, and    [30] G. C. H. E. De Croon, E. De Weerdt, C. De Wagter, B. D. W. Remes, and
A. Ollero, “Benchmark evaluation of hybrid fixed-flapping wing aerial        R. Ruijsink, “The appearance variation cue for obstacle avoidance,” IEEE
robot with autopilot architecture for autonomous outdoor flight opera-         Trans. Robot., vol. 28, no. 2, pp. 529–534, Apr. 2012.
tions,” IEEE Robot. Autom. Lett., vol. 8, no. 7, pp. 4243–4250, Jul. 2023.    [31] C. De Wagter, S. Tijmons, B. D. W. Remes, and G. C. H. E. de Croon,
[8] Q. Fu, S. Wang, J. Wang, X. Wu, and W. He, “Target detection and        “Autonomous flight of a 20-gram flapping wing MAV with a 4-gram
localization for flapping-wing robots based on biological eagle-eye vi-        onboard stereo vision system,” in Proc. IEEE Int. Conf. Robot. Autom.,
sion,” IEEE/ASME Trans. Mechatronics, vol. 30, no. 2, pp. 1590–1600,      May 2014, pp. 4982–4987.
Apr. 2025.                                                                [32]  J. Jiang, E. Pan, W. Xu, W. Sun, and J. Ye, “FWAF-VID: A flapping-wing
[9]  S. R. Nekoo and A. Ollero, “Closed-loop nonlinear optimal control design         aggressive flight benchmark dataset for visual-inertial localization,” IEEE
for flapping-wing flying robot (1.6 m wingspan) in indoor confined space:         Robot. Autom. Lett., vol. 10, no. 6, pp. 6328–6335, Jun. 2025.
Prototyping, modeling, simulation, and experiment,” ISA Trans., vol. 142,    [33]  J. P. Rodriguez-Gomez et al., “The GRIFFIN perception dataset: Bridging
pp. 635–652, Nov. 2023.                                                         the gap between flapping-wing flight and robotic perception,” IEEE Robot.
[10] K. Y. W. Scheper, M. Karasek, C. De Wagter, B. D. W. Remes, and G.        Autom. Lett., vol. 6, no. 2, pp. 1066–1073, Apr. 2021.
C. H. E. De Croon, “First autonomous multi-room exploration with an    [34] A. G. Eguiluz et al., “Why fly blind? Event-based visual guidance for
insect-inspired flapping wing vehicle,” in Proc. IEEE Int. Conf. Robot.         ornithopter robot flight,” in Proc. IEEE/RSJ Int. Conf. Intell. Robots Syst.,
Autom., May 2018, pp. 5546–5552.                                           Sep. 2021, pp. 1958–1965.
[11] M. Karásek, F. T. Muijres, C. De Wagter, B. D. W. Remes, and G. C.    [35]  J. Lee, S. Ryu, T. Kim, W. Kim, and H. J. Kim, “Learning-based path
H. E. de Croon, “A tailless aerial robotic flapper reveals that flies use         tracking control of a flapping-wing micro air vehicle,” in Proc. IEEE/RSJ
torque coupling in rapid banked turns,” Science, vol. 361, no. 6407,           Int. Conf. Intell. Robots Syst., Oct. 2018, pp. 7096–7102.
pp. 1089–1094, Sep. 2018.                                                [36] C. Chen, B. Song, Q. Fu, and J. Gao, “Autonomous navigation system
[12]  S. Tijmons, G. C. H. E. de Croon, B. D. W. Remes, C. De Wagter, and          for flapping wing aerial vehicle based on event-trigger planner,” Chin. J.
M. Mulder, “Obstacle avoidance strategy using onboard stereo vision on         Aeronaut., vol. 38, May 2025, Art. no. 103576.
a flapping wing MAV,” IEEE Trans. Robot., vol. 33, no. 4, pp. 858–874,    [37] G. Sachs, “Comparison of power requirements: Flapping vs. fixed wing
Aug. 2017.                                                                         vehicles,” Aerospace, vol. 3, no. 4, Sep. 2016, Art. no. 31.
[13] H. V. Phan, S. Aurecianus, T. K. L. Au, T. Kang, and H. C. Park, “To-    [38] A. Chen, B. Song, Z. Wang, K. Liu, D. Xue, and X. Yang, “Experimental
wards the long-endurance flight of an insect-inspired, tailless, two-winged,         study on the effect of increased downstroke duration for an FWAV with
flapping-wing flying robot,” IEEE Robot. Autom. Lett., vol. 5, no. 4,        morphing-coupled wing flapping configuration,” J. Bionic. Eng., vol. 21,
pp. 5059–5066, Oct. 2020.                                                      pp. 192–208, Oct. 2023.
[14] H.V.PhanandH.C.Park,“Mechanismsofcollisionrecoveryinflyingbee-    [39] E. Yesilbas, B. Özgür, E. Ozen, and T. Oktay, “Simultaneous and stochastic
tlesandflapping-wingrobots,”Science,vol.370,no.6521,pp. 1214–1219,         design of piston-prop TUAV vertical tail and its autonomous system,”
Dec. 2020.                                                       AEAT, vol. 97, no. 4, pp. 395–404, Apr. 2025.
[15] Y.-W. Chin et  al., “Efficient flapping wing drone arrests high-speed    [40] N. Ünal, Y. Öz, E. A. Ünal, and T. Oktay, “Enhancing aerodynamic
flight using post-stall soaring,” Sci. Robot., vol. 5, no. 44, Jul. 2020,        performance of a two-dimensional airfoil using plasma actuators,” Aerosp.
Art. no. eaba2386.                                                                      Sci. Technol., vol. 158, Mar. 2025, Art. no. 109882.
[16] R. Zufferey et al., “Design of the high-payload flapping wing robot E-flap,”    [41]  J. Grauer, E. Ulrich, J. Hubbard, D. Pines, and J. S. Humbert, “Testing
IEEE Robot. Autom. Lett., vol. 6, no. 2, pp. 3097–3104, Apr. 2021.             and system identification of an ornithopter in longitudinal flight,” J. Aircr.,
[17] E. Pan, H. Xu, H. Yuan, J. Peng, and W. Xu, “HIT-Hawk and HIT-Phoenix:          vol. 48, no. 2, pp. 660–667, Mar. 2011.
Two kinds of flapping-wing flying robotic birds with wingspans beyond 2
meters,” Biomimetic Intell. Robot., vol. 1, Jun. 2021, Art. no. 100002.
Authorized licensed use limited to: Shanghai Jiaotong University. Downloaded on March 24,2026 at 10:59:11 UTC from IEEE Xplore. Restrictions apply.