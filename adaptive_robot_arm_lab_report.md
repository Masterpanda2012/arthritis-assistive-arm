# Lab Report

## Title

Gesture-Controlled Assistive Robotic Arm

## What It Is

This project is a multi-modal assistive robotic arm that can receive commands in more than one way. Instead of only using buttons or a joystick, the arm can respond to voice commands, hand gestures detected by a webcam, computer vision, and sensor input. The arm is built around an Arduino Mega microcontroller and a set of servo motors that control the base rotation, lift, wrist rotation, and claw.

The main goal of this project was to make a robotic arm that feels more flexible and accessible. If one type of control is difficult or impossible for someone to use, another control method can still be used. This makes the arm more than a robotics demonstration. It is meant to be a tool that could help people interact with objects in a way that fits their physical abilities and preferences.

## Purpose

The purpose of this experiment was to test whether a gesture-controlled and AI-assisted robotic arm could move its pincer to a target more effectively than a basic manual control method.

I wanted to see if using a combination of gesture control, voice commands, vision, and distance sensing would make the arm easier and more accurate to use, especially for someone who may have limited mobility or difficulty using a traditional controller. Since the pincer on my prototype cannot reliably pick up many objects yet, the experiment focused on reaching and aligning with a target instead of lifting and carrying an object.

## Why I Chose This

The inspiration for this project came from two places: my grandfather's struggles with limited mobility, and a deeper realization that his experience is not unique. Watching someone I cared about lose the ability to independently manage everyday tasks made the problem feel real to me.

At the same time, I learned that millions of people worldwide face physical disabilities, tremors, weakness, or neurological conditions that make small tasks difficult. Even though robotics and artificial intelligence have advanced a lot, many assistive technologies are still expensive, complicated, or not designed for people who need a more natural way to control them.

That is why I wanted this project to respond to simple human actions, like speaking or moving a hand, instead of requiring a person to use a complicated controller. I wanted to explore whether a robot arm could be made more intuitive and supportive through a system that adapts to the user.

## Research Question

Does using gesture control and other adaptive input methods improve the performance and usability of a robotic arm compared to controlling it manually?

## Hypothesis

If the robotic arm is controlled using an adaptive system with hand gestures, voice commands, webcam vision, distance sensing, and AI-assisted intent interpretation, then it will move the pincer to a target faster and with fewer corrections than manual control, because the system gives the user more natural ways to communicate with the arm and uses sensor feedback to guide movement.

## Variables

### Independent Variable

The control method used to operate the robotic arm:

- Test A: Manual control using the control panel or typed commands
- Test B: Adaptive control using gestures, voice commands, computer vision, distance sensing, and AI assistance

### Dependent Variables

Robot arm performance, measured by:

- Time taken to complete each task
- Number of successful target reaches
- Number of failed attempts
- Number of corrections needed during movement
- Distance from the pincer to the target at the end of the trial
- Overall success rate
- Ease of use, based on how naturally the control method worked

### Controlled Variables

To keep the experiment fair, these factors were kept the same:

- Same robotic arm
- Same Arduino Mega and computer
- Same workspace
- Same target object or marked target zone
- Same starting position for the target
- Same starting position for the arm
- Same number of trials for each test
- Same lighting conditions as much as possible
- Same movement speed settings
- Same servo angle limits
- Same person operating the arm
- Same safety rules for every trial

## Materials

- Robotic arm frame
- Arduino Mega
- Servo motors for base, lift, wrist, and claw movement
- 28BYJ-48 stepper motor and ULN2003 driver, if used for lift testing
- Computer running the Python control program
- USB cable for Arduino serial communication
- Webcam
- Built-in computer microphone
- Vosk speech recognition model
- MediaPipe hand tracking
- YOLO object detection model
- TF-Luna LiDAR sensor or HC-SR04 ultrasonic sensor
- Breadboard and jumper wires
- Power supply
- Small target object or marked target zone, such as a taped circle, foam block, or paper cup
- Ruler or measuring tape for measuring final pincer distance from the target
- Stopwatch or timer
- Data table for recording results

## Procedure

### Phase 1: Building and Connecting the System

1. Assemble the robotic arm and attach the motors needed for base rotation, lift, wrist rotation, and claw movement.
2. Connect the motors to the Arduino Mega.
3. Connect the Arduino Mega to the computer using a USB cable.
4. Connect the webcam, microphone, and distance sensor.
5. Open the Python control program on the computer.
6. Confirm that the Arduino and computer are communicating through the serial connection.
7. Move the arm to its home position before testing.

### Phase 2: Testing the Inputs

8. Test the basic manual controls, including open claw, close claw, lift up, lower arm, rotate left, rotate right, and home.
9. Test the voice command system with simple commands such as "open claw," "close claw," and "home."
10. Test the gesture system by moving a hand in front of the webcam and checking whether the program recognizes the gesture.
11. Test the vision system to see whether the webcam and object detection model can identify the target object or target zone.
12. Test the distance sensor to check whether it gives a usable reading near the target.

### Phase 3: Setting Up the Experiment

13. Choose one small target object or create a marked target zone on the table.
14. Mark the target's position so it stays the same for every trial.
15. Decide what counts as success. For this experiment, a trial was successful if the pincer stopped within 3 cm of the target and closed around or tapped the target area without knocking it far away.
16. Decide how many trials will be completed for each condition. For example, complete 5 manual-control trials and 5 adaptive-control trials.
17. Prepare a data table for time, success or failure, corrections, and final distance from the target.

### Phase 4: Test A - Manual Control

18. Place the target at the marked position.
19. Move the robotic arm to the home position.
20. Start the timer.
21. Use only the manual control panel or typed commands to move the pincer toward the target.
22. Stop the timer when the pincer closes around or taps the target area.
23. Record the completion time.
24. Record whether the trial was successful or unsuccessful.
25. Record how many corrections were needed.
26. Measure and record the final distance between the pincer and the center of the target.
27. Return the target and arm to their starting positions.
28. Repeat this process for all manual-control trials.

### Phase 5: Test B - Adaptive Control

29. Place the target at the same marked position.
30. Move the robotic arm to the home position.
31. Start the timer.
32. Use the adaptive system, including gestures, voice commands, vision, distance sensing, and AI assistance, to move the pincer toward the target.
33. Stop the timer when the pincer closes around or taps the target area.
34. Record the completion time.
35. Record whether the trial was successful or unsuccessful.
36. Record how many corrections were needed.
37. Measure and record the final distance between the pincer and the center of the target.
38. Return the target and arm to their starting positions.
39. Repeat this process for all adaptive-control trials.

### Phase 6: Data Analysis

40. Calculate the average completion time for the manual-control trials.
41. Calculate the average completion time for the adaptive-control trials.
42. Calculate the success rate for each condition.
43. Calculate the average number of corrections for each condition.
44. Calculate the average final distance from the pincer to the target.
45. Compare the two control methods to see which one was more effective.

## Data Collection and Analysis

The completion times for all trials in each condition were added together and divided by the number of trials.

Average Completion Time = Total Completion Time divided by Number of Trials

The success rate was calculated by dividing the number of successful trials by the total number of trials and multiplying by 100.

Success Rate = Successful Trials divided by Total Trials multiplied by 100

The average number of corrections was calculated by adding all corrections and dividing by the number of trials.

Average Corrections = Total Corrections divided by Number of Trials

The average final distance was calculated by adding the final pincer-to-target distances and dividing by the number of trials.

Average Final Distance = Total Final Distance divided by Number of Trials

The improvement in completion time was calculated by subtracting the adaptive-control average time from the manual-control average time.

Time Improvement = Manual Control Average Time - Adaptive Control Average Time

## Results Table

| Trial | Manual Time (s) | Manual Success/Failure | Manual Corrections | Manual Final Distance (cm) | Adaptive Time (s) | Adaptive Success/Failure | Adaptive Corrections | Adaptive Final Distance (cm) |
|---|---:|---|---:|---:|---:|---|---:|---:|
| 1 | 52 | Success | 3 | 2.6 | 39 | Success | 1 | 1.8 |
| 2 | 59 | Success | 4 | 2.9 | 35 | Success | 1 | 1.5 |
| 3 | 66 | Failure | 6 | 4.8 | 44 | Success | 2 | 2.4 |
| 4 | 55 | Success | 3 | 2.7 | 41 | Success | 2 | 2.1 |
| 5 | 62 | Failure | 5 | 3.6 | 48 | Failure | 4 | 3.4 |
| Average | 58.8 | 3/5 successful | 4.2 | 3.32 | 41.4 | 4/5 successful | 2.0 | 2.24 |

## Results Summary

In the manual-control trials, the robotic arm reached the target successfully in 3 out of 5 trials. The average completion time was 58.8 seconds, the arm needed an average of 4.2 corrections per trial, and the average final distance from the target was 3.32 cm. The manual controls worked, but they required many small adjustments because every movement had to be controlled step by step.

In the adaptive-control trials, the robotic arm reached the target successfully in 4 out of 5 trials. The average completion time was lower at 41.4 seconds, the average number of corrections dropped to 2.0 per trial, and the average final distance from the target was 2.24 cm. This means the adaptive system was about 17.4 seconds faster on average.

Time Improvement = 58.8 seconds - 41.4 seconds = 17.4 seconds

The adaptive system had a higher success rate, faster average time, fewer corrections, and better final accuracy. This suggests that gestures, voice commands, computer vision, and sensor feedback helped make the arm easier to control, even though the pincer was not strong enough to reliably pick objects up.

## Challenges

Building this project required solving problems across hardware, software, and system integration. One of the biggest challenges was making all the parts communicate reliably. The Arduino, Python program, webcam, microphone, gesture system, vision model, and distance sensor all had to work together at the right time.

Another challenge was gesture recognition. At first, the system could confuse random hand movements with real commands, so the program needed filtering and confirmation logic to avoid accidental actions. Voice control also had challenges because background noise or unclear speech could cause the program to misunderstand a command.

The vision system was difficult because the camera needed good lighting and a clear view of the object. The distance sensor also needed stable readings so the arm could understand where the object was. Physically building the arm was another challenge because the motors had to be strong enough, the wiring had to stay connected, and the arm had to move without shaking too much.

## Conclusion

This experiment tested whether an adaptive robotic arm could move its pincer to a target more easily and accurately. The project was designed around the idea that assistive technology should be flexible, because different people have different abilities and needs.

The results supported the hypothesis. The adaptive-control system had a higher success rate than manual control, completing 4 out of 5 trials successfully compared to 3 out of 5 for manual control. It was also faster and more accurate. Manual control took an average of 58.8 seconds, while adaptive control took 41.4 seconds. Manual control needed 4.2 corrections per trial, while adaptive control needed 2.0 corrections per trial. The average final distance from the target also improved from 3.32 cm to 2.24 cm.

This shows that the adaptive system made the arm more efficient, even though it still had some problems. The failed adaptive trial happened because the target was not detected clearly and the pincer stopped slightly outside the success range. Overall, the project shows how robotics, computer vision, gesture recognition, voice commands, and AI can work together to create a more natural way for people to control machines. It also shows a realistic next step for the project: improving the pincer so the arm can move from reaching targets to actually gripping and lifting objects.

## Possible Sources of Error

- Voice commands may be misheard if the room is noisy.
- Hand gestures may be detected incorrectly if the lighting is poor.
- Random hand movements may accidentally look like commands.
- The webcam may struggle if the target blends into the background.
- The distance sensor may give inaccurate readings if the target is angled or reflective.
- Servo or stepper motors may move slightly differently between trials.
- The target may not be placed in exactly the same position every time.
- Measuring the final distance by hand may introduce small errors.
- The operator may improve with practice during later trials.

## Improvements

- Run more trials to make the results more reliable.
- Test the system with different users, especially people with different mobility needs.
- Compare each input method separately, such as manual control, voice only, gesture only, and full adaptive control.
- Improve gesture filtering so accidental commands are less likely.
- Improve object detection by testing different lighting and backgrounds.
- Add automatic logging so the program records time, commands, sensor readings, and errors.
- Calibrate the camera and distance sensor before each testing session.
- Improve the pincer grip so future tests can measure real picking up and placing.
- Make the robotic arm stronger and more stable so it can handle objects more smoothly.
