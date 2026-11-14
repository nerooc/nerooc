# Asset credits and maintenance

This profile uses Code & Visuals artwork and project imagery supplied by Tomasz Gajda. Project captures show Waymark Garage, Hevelis and the [Rerousel demo](https://rerousel.netlify.app/). Brand names and marks belong to their respective owners.

- Technology and LinkedIn marks: [Devicon](https://github.com/devicons/devicon) ([MIT notice](assets/icons/LICENSE-DEVICON.txt)) and [Simple Icons](https://github.com/simple-icons/simple-icons) ([CC0 notice](assets/icons/LICENSE-SIMPLE-ICONS.md)). Pinned technology sources are recorded in [sources.json](assets/icons/sources.json).
- Outlined typography: Hanken Grotesk and Fraunces, under the SIL Open Font License. Full notices: [Hanken Grotesk](assets/type/HANKEN-OFL.txt) and [Fraunces](assets/type/FRAUNCES-OFL.txt).
- The Code & Visuals wordmark and street illustration use the studio's original artwork.

SVGs include their fonts, icons and screenshots. No external image service is required to display them. Animation respects reduced-motion preferences.

## Activity calendar

The calendar is a dated snapshot of public GitHub contributions. Automatic refresh is not enabled in this repository. Refresh it with Python 3.10 or newer, then commit and push the generated activity files:

```sh
python3 scripts/update_activity.py
```

Reproduce the checked-in snapshot without network access:

```sh
python3 scripts/update_activity.py --from-json assets/activity/snapshot.json
```
