"""Example: a complete EV battery passport built from the IDTA templates.

All values are fictional ("Voltaris EV-86" pack, example.com identifiers) and only illustrate the
structure required by Regulation (EU) 2023/1542 Annex XIII.

Usage: python3 examples/battery_passport.py [out.aasx]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GTIN = '04012345000016'
SERIAL = 'VX86-2026-000417'
NOW = '2026-10-20T09:00:00Z'


def spec(base='https://id.example.com'):
    return {
        'gtin': GTIN, 'serial': SERIAL, 'granularity': 'Item', 'name': 'Voltaris EV-86 traction battery',
        'operatorId': 'urn:example:operator:voltaris-gmbh', 'facilityId': 'urn:example:facility:voltaris-plant-1',
        'base': base, 'idPrefix': 'urn:example:voltaris:ev86:000417',
        'values': {
            'dbp-nameplate': {
                'URIOfTheProduct': f'{base}/01/{GTIN}/21/{SERIAL}',
                'ManufacturerName': 'Voltaris GmbH', 'SerialNumber': SERIAL, 'DateOfManufacture': '2026-09-14',
                'UniqueFacilityIdentifier': 'urn:example:facility:voltaris-plant-1', 'LifeCycleStage': 'Original',
                'ManufacturerIdentifier': 'urn:example:operator:voltaris-gmbh',
                'Markings[0].MarkingName': 'CE', 'Markings[1].MarkingName': 'Separate collection (crossed-out bin)',
                'EUDeclarationOfConformity[0]': 'https://docs.example.com/voltaris/ev86/doc.pdf',
                'ResultsOfTestReportsProvingCompliance[0]': 'restricted: see authority documents',
            },
            'dbp-technical-data': {
                'GeneralInformation.ManufacturerName': 'Voltaris GmbH',
                'GeneralInformation.ManufacturerIdentifier': 'urn:example:operator:voltaris-gmbh',
                'GeneralInformation.BatteryCategory': 'EV battery', 'GeneralInformation.BatteryMass': 452.0,
                'GeneralInformation.ProductImages[0]': 'https://docs.example.com/voltaris/ev86/pack.png',
                'GeneralInformation.WarrantyInformation.WarrantyPeriod': '8 years or 160000 km',
                'TechnicalPropertyAreas.CapacityEnergyVoltage.NominalVoltage': 400.0,
                'TechnicalPropertyAreas.CapacityEnergyVoltage.MinVoltage': 300.0,
                'TechnicalPropertyAreas.CapacityEnergyVoltage.MaxVoltage': 470.0,
                'TechnicalPropertyAreas.CapacityEnergyVoltage.RatedCapacity': 215.0,
                'TechnicalPropertyAreas.CapacityEnergyVoltage.CertifiedUsableBatteryEnergy': 82.5,
                'TechnicalPropertyAreas.CapacityEnergyVoltage.CapacityFade': 0.8,
                'TechnicalPropertyAreas.RoundTripEnergyEfficiency.EnergyRoundTripEfficiencyFade': 0.3,
                'TechnicalPropertyAreas.Lifetime.CapacityThresholdExhaustion': 70.0,
                'TechnicalPropertyAreas.RoundTripEnergyEfficiency.InitialRoundTripEnergyEfficiency': 95,
                'TechnicalPropertyAreas.RoundTripEnergyEfficiency.RoundTripEnergyEfficiencyAt50PercentOfCycleLife': 92,
                'TechnicalPropertyAreas.Resistance.InitialInternalResistanceOnBatteryCellLevel': 0.0011,
                'TechnicalPropertyAreas.Resistance.InitialInternalResistanceOnBatteryPackLevel': 0.085,
                'TechnicalPropertyAreas.Resistance.InternalResistanceIncreaseOfBatteryPackLevel': 0.0,
                'TechnicalPropertyAreas.PowerCapability.MaximumPermittedBatteryPower': 250.0,
                'TechnicalPropertyAreas.PowerCapability.PowerFade': 0.0,
                'TechnicalPropertyAreas.PowerCapability.OriginalPowerCapability[0].atSoc': 80,
                'TechnicalPropertyAreas.PowerCapability.OriginalPowerCapability[0].powerCapabilityAt': 240.0,
                'TechnicalPropertyAreas.PowerCapability.OriginalPowerCapability[1].atSoc': 20,
                'TechnicalPropertyAreas.PowerCapability.OriginalPowerCapability[1].powerCapabilityAt': 180.0,
                'TechnicalPropertyAreas.Temperature.TemperatureRangeIdleState_LowerBoundary': -30.0,
                'TechnicalPropertyAreas.Temperature.TemperatureRangeIdleState_UpperBoundary': 55.0,
                'TechnicalPropertyAreas.Lifetime.ExpectedLifetimeInCalendarYears': 12,
                'TechnicalPropertyAreas.Lifetime.ExpectedNumberOfCycles': 2500,
                'TechnicalPropertyAreas.Lifetime.CRateOfRelevantCycleLifeTest': 0.5,
            },
            'dbp-carbon-footprint': {
                'ProductCarbonFootprints[0].PcfCalculationMethods[0]': 'EU Battery Regulation Annex II method',
                'ProductCarbonFootprints[0].PcfCO2eq': 61.4,
                'ProductCarbonFootprints[0].ReferenceImpactUnitForCalculation': 'kWh',
                'ProductCarbonFootprints[0].QuantityOfMeasureForCalculation': 82.5,
                'ProductCarbonFootprints[0].LifeCyclePhases[0]': 'A1-A3 raw material acquisition and pre-processing',
                'ProductCarbonFootprints[0].PerformanceClass': 'B',
                'ProductCarbonFootprints[0].WebLinkToPublicCarbonFootprintStudy[0]':
                    'https://docs.example.com/voltaris/ev86/carbon-footprint-study.pdf',
            },
            'dbp-product-condition': {
                'EnergyThroughput.EnergyThroughputValue': 1240.0, 'EnergyThroughput.LastUpdate': NOW,
                'StateOfCharge.StateOfChargeValue': 64.0, 'StateOfCharge.LastUpdate': NOW,
                'CapacityThroughput.CapacityThroughputValue': 3100.0, 'CapacityThroughput.LastUpdate': NOW,
                'NumberOfFullCycles.NumberOfFullCyclesValue': 15, 'NumberOfFullCycles.LastUpdate': NOW,
                'StateOfCertifiedEnergy.StateOfCertifiedEnergyValue': 99.2, 'StateOfCertifiedEnergy.LastUpdate': NOW,
                'RemainingEnergy.RemainingEnergyValue': 81.8, 'RemainingEnergy.LastUpdate': NOW,
                'RemainingCapacity.RemainingCapacityValue': 213.4, 'RemainingCapacity.LastUpdate': NOW,
                'NegativeEvents[0].NegativeEventValue': 'none recorded', 'NegativeEvents[0].LastUpdate': NOW,
                'TemperatureInformation.MeasuredTemp': 21.5, 'TemperatureInformation.TimeExtremeHighTemp': 0.0,
                'TemperatureInformation.LastUpdate': NOW,
            },
            'dbp-material-composition': {
                'BatteryChemistry.ShortName': 'NMC811', 'BatteryChemistry.ClearName': 'Lithium nickel manganese cobalt oxide',
                'BatteryMaterials[0].BatteryMaterialIdentifier': '7440-02-0', 'BatteryMaterials[0].BatteryMaterialName': 'Nickel',
                'BatteryMaterials[0].BatteryMaterialMass': 39.0, 'BatteryMaterials[0].IsCriticalRawMaterial': True,
                'BatteryMaterials[1].BatteryMaterialIdentifier': '7439-93-2', 'BatteryMaterials[1].BatteryMaterialName': 'Lithium',
                'BatteryMaterials[1].BatteryMaterialMass': 6.1, 'BatteryMaterials[1].IsCriticalRawMaterial': True,
                'BatteryMaterials[2].BatteryMaterialIdentifier': '7440-48-4', 'BatteryMaterials[2].BatteryMaterialName': 'Cobalt',
                'BatteryMaterials[2].BatteryMaterialMass': 4.8, 'BatteryMaterials[2].IsCriticalRawMaterial': True,
                'HazardousSubstances[0].HazardousSubstanceName': 'Lithium hexafluorophosphate',
                'HazardousSubstances[0].HazardousSubstanceIdentifier': '21324-40-3',
                'HazardousSubstances[0].HazardousSubstanceClass': 'Acute toxicity; skin corrosion',
            },
            'dbp-circularity': {
                'RecycledContentInformation[0].RecycledMaterial': 'Nickel',
                'RecycledContentInformation[0].PreConsumerShare': 4.0, 'RecycledContentInformation[0].PostConsumerShare': 2.0,
                'RecycledContentInformation[1].RecycledMaterial': 'Cobalt',
                'RecycledContentInformation[1].PreConsumerShare': 12.0, 'RecycledContentInformation[1].PostConsumerShare': 4.0,
                'RecycledContentInformation[2].RecycledMaterial': 'Lithium',
                'RecycledContentInformation[2].PreConsumerShare': 4.0, 'RecycledContentInformation[2].PostConsumerShare': 2.0,
                'RenewableContent': 8.0,
                'DismantlingAndRemovalInformation[0]': 'https://docs.example.com/voltaris/ev86/dismantling-manual.pdf',
                'SparePartSources[0].NameOfSupplier': 'Voltaris Service GmbH',
                'SparePartSources[0].AddressOfSupplier.NationalCode': 'DE',
                'SparePartSources[0].AddressOfSupplier.PostalCode': '10115',
                'SparePartSources[0].AddressOfSupplier.Street': 'Beispielstrasse 1',
                'SparePartSources[0].EmailAddressOfSupplier.EmailAddress': 'parts@example.com',
                'SparePartSources[0].SupplierWebAddress': 'https://parts.example.com/voltaris',
                'SparePartSources[0].Components[0].PartName': 'Battery management system board',
                'SparePartSources[0].Components[0].PartNumber': 'VX-BMS-86-02',
                'SparePartSources[0].Components[1].PartName': 'Cell module (12s)',
                'SparePartSources[0].Components[1].PartNumber': 'VX-MOD-86-12',
                'SafetyMeasures.SafetyInstructions[0]': 'https://docs.example.com/voltaris/ev86/safety.pdf',
                'SafetyMeasures.ExtinguishingAgents[0]': 'Water (large quantities, cooling)',
                'EndOfLifeInformation.WastePrevention[0]': 'Return to an authorised treatment facility; reuse and repurposing preferred.',
                'EndOfLifeInformation.SeparateCollection[0]': 'Do not dispose of with household waste; separate collection is mandatory.',
                'EndOfLifeInformation.InformationOnCollection[0]': 'https://www.example.com/voltaris/take-back',
            },
            'dbp-handover': {
                'Documents[0].DocumentClassifications[0].ClassName': 'Certificate, declaration',
                'Documents[0].DocumentClassifications[0].ClassId': '02-04',
                'Documents[0].DocumentClassifications[0].ClassificationSystem': 'VDI 2770 Blatt 1:2020',
                'Documents[0].DocumentIds[0].DocumentDomainId': 'voltaris', 'Documents[0].DocumentIds[0].DocumentIdentifier': 'DOC-EV86-01',
                'Documents[0].DocumentVersions[0].Language[0]': 'en',
                'Documents[0].DocumentVersions[0].Title': 'EU declaration of conformity',
                'Documents[0].DocumentVersions[0].DigitalFiles[0]': 'https://docs.example.com/voltaris/ev86/doc.pdf',
            },
        },
        'extraParts': [{
            'template': 'dbp-handover', 'idShort': 'AuthorityTestReports', 'tier': 'authority',
            'values': {
                'Documents[0].DocumentClassifications[0].ClassName': 'Test report',
                'Documents[0].DocumentClassifications[0].ClassId': '03-03',
                'Documents[0].DocumentClassifications[0].ClassificationSystem': 'VDI 2770 Blatt 1:2020',
                'Documents[0].DocumentIds[0].DocumentDomainId': 'voltaris', 'Documents[0].DocumentIds[0].DocumentIdentifier': 'TR-EV86-UN38.3',
                'Documents[0].DocumentVersions[0].Language[0]': 'en',
                'Documents[0].DocumentVersions[0].Title': 'Safety and performance test reports',
                'Documents[0].DocumentVersions[0].DigitalFiles[0]': 'https://restricted.example.com/voltaris/ev86/tests.zip',
            }}],
    }


def main():
    from aasforge.app import Config, Forge
    from aasforge.dpp import passport
    forge = Forge(Config(database=':memory:', dpp=True))
    result = passport.create(forge.repository, spec())
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('battery-passport.aasx')
    out.write_bytes(forge.repository.package([result['aasId']]))
    found, _ = passport.submodels_of(forge.store, forge.store.get('shell', result['aasId']))
    report = passport.completeness(found, passport.detect_category(found))
    print(f'wrote {out} ({out.stat().st_size} bytes); passport {result["passportId"]}')
    print(f'digital link {result["digitalLink"]}; completeness {report["present"]}/{report["mandatory"]} '
          f'({report["score"]}%) for category {report["category"]}')


if __name__ == '__main__':
    main()
