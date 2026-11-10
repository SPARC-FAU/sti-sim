# Your workspace

This folder is yours. It is an ordinary folder on your computer and appears as
`/workspace` inside the simulation. Edit it with any editor. The simulation
never changes or deletes it.

Put your ROS 2 packages in `src/`, then build them in a simulation terminal
(`python3 sim.py shell`):

```sh
cd /workspace
colcon build --symlink-install
source /colcon/install/setup.bash
```

New terminals source `/colcon/install/setup.bash` automatically. Start your nodes
with `--ros-args -p use_sim_time:=true`.

## Moving your code

This folder contains only your source code. Build output is kept separately in
`/colcon`, so nothing in here depends on your computer.

To switch to a new version of the simulation or to another computer (Windows,
macOS or Linux), copy this whole `workspace/` folder into the new simulation
folder, next to `sim.py`, and run `colcon build` again there.

Keeping this folder in your own Git repository is a good idea.
