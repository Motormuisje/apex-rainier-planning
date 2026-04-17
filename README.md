# SOP Clean Programma

Deze map bevat alleen de bestanden die nodig zijn om de applicatie als Python-programma te draaien.

## Starten

```powershell
pip install -r requirements.txt
python main.py
```

Daarna opent de app normaal op:

```text
http://localhost:5000
```

## Runtime data

Uploads, exports, sessies, configuratie en licentiegegevens worden standaard niet in deze map bewaard, maar in:

```text
%LOCALAPPDATA%\SOPPlanningEngine
```

Wil je dat tijdelijk ergens anders zetten, start dan met:

```powershell
$env:SOP_APP_DATA_DIR = "C:\pad\naar\data"
python main.py
```

## Bewust niet meegenomen

- testbestanden en test-output
- build-, dist- en exe-bestanden
- zip releases
- oude uploads en exports
- audit-, debug- en clientrapporten
- Python cachebestanden
