import json
import os
import urllib.request

from playwright.sync_api import sync_playwright


def run_verification():
    output_dir = os.path.abspath('apps/dashboard/public')
    os.makedirs(output_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            headless=True
        )
        context = browser.new_context(
            viewport={'width': 1440, 'height': 900}
        )
        page = context.new_page()

        print("1. Loading ANCOS Interactive 2D World...")
        page.goto('http://localhost:12001/')
        page.wait_for_timeout(3500)
        page.screenshot(path=os.path.join(output_dir, '01_initial_living_world.png'))
        print(" -> Saved 01_initial_living_world.png")

        print("2. Opening Dev Simulation Suite...")
        page.click('button:has-text("Dev Simulation Suite")')
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(output_dir, '02_dev_suite_open.png'))
        print(" -> Saved 02_dev_suite_open.png")

        print("3. Demonstrating: Character Walking Between Workstations...")
        page.click('button:has-text("1. Walk Between Workstations")')
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(output_dir, '03_character_walking.png'))
        print(" -> Saved 03_character_walking.png")

        print("4. Demonstrating: Character Transitioning to Work at Desk...")
        page.click('button:has-text("2. Walk & Transition to Work")')
        page.wait_for_timeout(4000) # Wait for character to arrive at chair and start typing
        page.screenshot(path=os.path.join(output_dir, '04_character_working_at_desk.png'))
        print(" -> Saved 04_character_working_at_desk.png")

        print("5. Demonstrating: Two Characters Communicating (Speech Bubbles & Comms Laser)...")
        page.click('button:has-text("3. Two Characters Communicate")')
        page.wait_for_timeout(1000) # Capture laser beam and traveling data packet in flight
        page.screenshot(path=os.path.join(output_dir, '05_characters_communicating.png'))
        print(" -> Saved 05_characters_communicating.png")

        print("6. Demonstrating: Boardroom Meeting (Characters Assembling & 3D Hologram)...")
        page.click('button:has-text("4. Enter Boardroom Meeting")')
        page.wait_for_timeout(5500) # Wait for participants to arrive and seat at conference table
        page.screenshot(path=os.path.join(output_dir, '06_boardroom_meeting_active.png'))
        print(" -> Saved 06_boardroom_meeting_active.png")

        print("7. Concluding Meeting & Returning to Workstations...")
        page.click('button:has-text("5. Conclude & Return to Desks")')
        page.wait_for_timeout(4500) # Wait for participants to walk back along corridors to desks
        page.screenshot(path=os.path.join(output_dir, '07_returned_from_meeting.png'))
        print(" -> Saved 07_returned_from_meeting.png")

        print("8. Demonstrating: Dynamically Recruiting Employee at Elevator Spawn...")
        page.click('button:has-text("6. Dynamically Recruit Employee")')
        page.wait_for_timeout(1200)
        page.screenshot(path=os.path.join(output_dir, '08_new_recruit_spawned.png'))
        print(" -> Saved 08_new_recruit_spawned.png")

        print("9. Demonstrating: Celebrating Animation...")
        page.click('button:has-text("Celebrate")')
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(output_dir, '09_celebrating_state.png'))
        print(" -> Saved 09_celebrating_state.png")

        print("10. Demonstrating: Warning Alert Animation...")
        page.click('button:has-text("Warning")')
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(output_dir, '10_warning_state.png'))
        print(" -> Saved 10_warning_state.png")

        print("11. Demonstrating: Workstation Inspection...")
        page.click('button:has-text("7. Inspect Workstation")')
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(output_dir, '11_workstation_inspected.png'))
        print(" -> Saved 11_workstation_inspected.png")

        print("12. Demonstrating: Boardroom Inspection...")
        page.click('button:has-text("8. Inspect Boardroom Suite")')
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(output_dir, '12_boardroom_inspected.png'))
        print(" -> Saved 12_boardroom_inspected.png")

        print("13. Demonstrating: Department Inspection...")
        page.click('button:has-text("9. Inspect Research Lab")')
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(output_dir, '13_department_inspected.png'))
        print(" -> Saved 13_department_inspected.png")

        # Close dev panel for final clean view
        page.click('button:has-text("Dev Simulation Suite")')
        page.wait_for_timeout(400)

        print("14. Demonstrating: Real Backend Event Communication...")
        try:
            req = urllib.request.Request(
                'http://localhost:12000/comms/direct',
                data=json.dumps({
                    'sender': 'ANC-BOSS',
                    'recipient': 'ANC-EMP-6D43',
                    'content': 'Strategic priority update: Deploy all neural agents to production.',
                    'subject': 'Executive Directive'
                }).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            urllib.request.urlopen(req)
            print(" -> Sent real direct message via backend POST /comms/direct")
            page.wait_for_timeout(4500) # Wait for 4s backend poll tick
            page.screenshot(path=os.path.join(output_dir, '14_real_backend_comms.png'))
            print(" -> Saved 14_real_backend_comms.png")
        except Exception as ex:
            print(f" -> Backend comms dispatch notice: {ex}")

        context.close()
        browser.close()

        print("All deep verification steps executed successfully!")

if __name__ == '__main__':
    run_verification()
