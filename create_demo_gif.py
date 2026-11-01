import os

from PIL import Image


def make_gif():
    public_dir = os.path.abspath('apps/dashboard/public')
    frame_names = [
        '01_initial_living_world.png',
        '02_dev_suite_open.png',
        '03_character_walking.png',
        '04_character_working_at_desk.png',
        '05_characters_communicating.png',
        '06_boardroom_meeting_active.png',
        '07_returned_from_meeting.png',
        '08_new_recruit_spawned.png',
        '09_celebrating_state.png',
        '10_warning_state.png',
        '11_workstation_inspected.png',
        '12_boardroom_inspected.png',
        '13_department_inspected.png',
        '14_real_backend_comms.png',
    ]

    images = []
    for fname in frame_names:
        fpath = os.path.join(public_dir, fname)
        if os.path.exists(fpath):
            img = Image.open(fpath).convert('RGB')
            # Resize slightly to keep GIF lightweight & fast loading (800x500)
            img = img.resize((960, 600), Image.Resampling.LANCZOS)
            images.append(img)

    if images:
        out_gif = os.path.join(public_dir, 'simulation_demo.gif')
        images[0].save(
            out_gif,
            save_all=True,
            append_images=images[1:],
            duration=1200, # 1.2s per demonstration state
            loop=0
        )
        print(f"Successfully generated animated demonstration GIF: {out_gif}")
    else:
        print("No frames found to generate GIF")

if __name__ == '__main__':
    make_gif()

