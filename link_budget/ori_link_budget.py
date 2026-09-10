"""ORI Earth-Venus-Earth link budget -- the site dataclasses, SystemNoiseTemperature,
and EVELinkBudget classes extracted VERBATIM (class definitions only; the notebook's
example/demo statements are omitted) from the Open Research Institute notebook
Link_Budget_Modeling.ipynb (github.com/OpenResearchInstitute/EVE, GPL-3.0; authors
Michelle Thompson W5NYV et al.), code cells 1, 3 and 4, snapshot 2026-09-10.
Extracted so DSES can recompute its cases with ORI's own arithmetic (see dses_cases.py).
Do not edit the classes here; re-extract from the notebook when ORI updates it.
"""
import math
from dataclasses import dataclass
from typing import Optional, Dict, Any

import numpy as np


@dataclass
class DSESLinkParameters: #things that are true for the site regardless of the target
    # location of this dish in Google Earth is 38°22'51"N 103°09'22"W
    latitude: float = 38.380833 # from FCC converter
    longitude: float = -103.156111 # from FCC converter
    elevation: float = 1311 # meters, from online calculator
    default_elevation_deg: float = 30.0 # Venus elevation near conjunction
    receive_only: bool = False   # default - sites can transmit and receive
#    tx_frequency_mhz: float = 1296.0 # Transmit frequency
    tx_frequency_mhz: float = 2304.0 # Transmit frequency anticipated for October 2026
    tx_power_w: float = 1500.0 # Transmit power in watts
    tx_antenna_diameter_m: float = 18.29 # Transmit antenna diameter
    tx_antenna_efficiency: float = 0.69 # Transmit antenna efficiency
    rx_antenna_diameter_m: float = 18.29 # Receive antenna diameter
    rx_antenna_efficiency: float = 0.69 # Receive antenna efficiency
    tx_line_loss_db: float = 0.5 # provided
    rx_line_loss_db: float = 0.5 # provided
    pointing_error_deg: float = 0.01 # provided
    lna_noise_figure_db: float = 0.4 # from datasheet for the Kuhne MKU LNA 132 AH SMA
    lna_noise_figure_K: float = 290 * (10**(lna_noise_figure_db/10) - 1) # calculated from lna_noise_figure_db
    receiver_noise_bandwidth: float = 100e3 # operational receiver bandwidth. 
    c: float = 299792458  # Speed of light in m/s
    k: float = 1.380649e-23  # Boltzmann constant in J/K
    t0: float = 290  # Reference temperature in K


@dataclass
class DwingelooLinkParameters: #things that are true for the site regardless of the target
    latitude: float = 52.81213723180477 # from Thomas
    longitude: float = 6.396346463227839 # from Thomas
    elevation: float = 70.26 # from Thomas
    default_elevation_deg: float = 20.0 # Venus elevation near conjunction
    receive_only: bool = False   # default - sites can transmit and receive
    tx_frequency_mhz: float = 1299.5 # Transmit frequency from Thomas
#    tx_frequency_mhz: float = 2304.0 # Transmit frequency anticipated for October 2026
    tx_power_w: float = 1000.0 # Transmit power in watts
    tx_antenna_diameter_m: float = 25 # Transmit antenna diameter from Thomas
    tx_antenna_efficiency: float = 0.69 # Transmit antenna efficiency from Thomas
    rx_antenna_diameter_m: float = 25 # Receive antenna diameter from Thomas
    rx_antenna_efficiency: float = 0.69 # Receive antenna efficiency from Thomas
    tx_line_loss_db: float = 0.5 # best guess
    rx_line_loss_db: float = 0.5 # best guess
    pointing_error_deg: float = 0.01 # best guess
    lna_noise_figure_db: float = 0.629 # best guess working backwards from a Tsys = 65 measured recently from Thomas
    lna_noise_figure_K: float = 290 * (10**(lna_noise_figure_db/10) - 1) # calculated from lna_noise_figure_db
    receiver_noise_bandwidth: float = 100e3 # operational receiver bandwidth. 
    c: float = 299792458  # Speed of light in m/s
    k: float = 1.380649e-23  # Boltzmann constant in J/K
    t0: float = 290  # Reference temperature in K


@dataclass
class BochumLinkParameters:
    # AMSAT-DL dish, Bochum, Germany
    # Contact: AMSAT-DL / Bochum team for verification of all RF parameters
    # Location from published sources — confirm with contact
    latitude: float = 51.440000    # degrees N  # VERIFY
    longitude: float = 7.210000    # degrees E  # VERIFY
    elevation: float = 100.0       # meters     # VERIFY
    default_elevation_deg: float = 20.0 # Venus elevation near conjunction
    receive_only: bool = False   # might be receive only for October 2026
    tx_frequency_mhz: float = 2304.0            # VERIFY — confirm they can tx at 2304
    tx_power_w: float = 250.0                  # 2009 used magnetron ~7kW, current setup is 250 W as per P. Gülzow
    tx_antenna_diameter_m: float = 20.0
    tx_antenna_efficiency: float = 0.69         # VERIFY — assumed typical parabolic, not Bochum-specific
    rx_antenna_diameter_m: float = 20.0
    rx_antenna_efficiency: float = 0.69         # VERIFY — assumed, not measured
    tx_line_loss_db: float = 0.5                # VERIFY
    rx_line_loss_db: float = 0.5                # VERIFY
    pointing_error_deg: float = 0.01            # VERIFY
    lna_noise_figure_db: float = 0.5            # VERIFY — no datasheet value in hand
    lna_noise_figure_K: float = 290 * (10**(lna_noise_figure_db/10) - 1)  # derived
    receiver_noise_bandwidth: float = 100e3     # VERIFY - this is a guess
    c: float = 299792458
    k: float = 1.380649e-23
    t0: float = 290


@dataclass
class GBTLinkParameters:
    # Green Bank Telescope — receive-only for EVE! need bistatic approach!
    # National Radio Quiet Zone — no transmission possible
    latitude: float = 38.433     # degrees N  # VERIFY via GBO
    longitude: float = -79.840   # degrees E  # VERIFY via GBO
    elevation: float = 807.0     # meters     # VERIFY via GBO
    default_elevation_deg: float = 32.0 # Venus elevation near conjunction
    receive_only: bool = True   # Green Bank is receive only
    tx_frequency_mhz: float = 2304.0          # not applicable — rx only, but we need to have a value here
    #tx_frequency_mhz: float = 1299.5          # not applicable — rx only, but we need to have a value here
    tx_power_w: float = 0.0                   # rx only
    tx_antenna_diameter_m: float = 100.0      # confirmed
    tx_antenna_efficiency: float = 0.71       # VERIFY — use GBT sensitivity calculator
    rx_antenna_diameter_m: float = 100.0      # confirmed
    rx_antenna_efficiency: float = 0.71       # VERIFY — use GBT sensitivity calculator
    tx_line_loss_db: float = 0.0              # rx only
    rx_line_loss_db: float = 0.5             # VERIFY
    pointing_error_deg: float = 0.001        # VERIFY — GBT pointing is exceptional
    lna_noise_figure_db: float = 0.3         # VERIFY — use GBT sensitivity calculator at 2304 MHz
    lna_noise_figure_K: float = 290 * (10**(lna_noise_figure_db/10) - 1)
    receiver_noise_bandwidth: float = 100e3
    c: float = 299792458
    k: float = 1.380649e-23
    t0: float = 290


@dataclass
class EffelsbergLinkParameters:
    # Effelsberg 100m Radio Telescope, Max Planck Institute for Radio Astronomy
    # Bad Münstereifel-Effelsberg, Germany
    # Coordinates, elevation, diameter, and pointing below are from the
    # MPIfR 100m telescope technical specifications page.
    # Remaining VERIFY flags are parameters the MPIfR tech-spec sheet does not cover.
    latitude:  float = 50.524833   # 50° 31' 29.4" N  (MPIfR spec)
    longitude: float =  6.883611   #  6° 53' 01.0" E  (MPIfR spec)
    elevation: float = 369.0       # intersection of main axes, m (MPIfR spec)
    default_elevation_deg: float = 20.0   # Venus elevation near conjunction
    # Operational elevation range is 8.1°–89° per MPIfR spec;
    # consider using 8.1° as min_elevation in visibility plots.
    receive_only: bool = True             # assume receive-only for EVE
    tx_frequency_mhz: float = 1299.5      # focus on 1299.5 for now
    tx_frequency_mhz: float = 2304.0      # 
    tx_power_w: float = 0.0               # receive-only placeholder
    tx_antenna_diameter_m: float = 100.0  # MPIfR spec (confirmed)
    tx_antenna_efficiency: float = 0.69   # VERIFY — assumed; Ruze surface η ≈ 1.00
                                          # at L/S-band from 0.5 mm RMS, but total
                                          # aperture efficiency requires MPIfR measurement
    rx_antenna_diameter_m: float = 100.0  # MPIfR spec (confirmed)
    rx_antenna_efficiency: float = 0.69   # VERIFY — same caveat as tx
    tx_line_loss_db: float = 0.5          # VERIFY — not in MPIfR spec sheet
    rx_line_loss_db: float = 0.5          # VERIFY — not in MPIfR spec sheet
    pointing_error_deg: float = 0.000556  # 2 arcsec effective (MPIfR spec: 1–2 arcsec)
    lna_noise_figure_db: float = 0.3      # VERIFY — depends on receiver choice
    lna_noise_figure_K: float = 290 * (10**(lna_noise_figure_db/10) - 1)
    receiver_noise_bandwidth: float = 100e3
    c: float = 299792458
    k: float = 1.380649e-23
    t0: float = 290


class SystemNoiseTemperature:
    """
    A class to calculate system noise temperature for radio systems.
    """
    
    def __init__(self, params: 'SiteLinkParameters'):
        """
        Initialize the system noise temperature calculator.
        
        Parameters:
        params (SiteLinkParameters): The link parameters object containing system settings
        """
        self.params = params
    
    def calculate_sky_noise(self, elevation_angle_deg: float, atmospheric_conditions: str = 'clear') -> float:
        """
        Calculate sky noise temperature based on frequency and elevation angle.
        
        Parameters:
        elevation_angle_deg (float): Elevation angle in degrees
        atmospheric_conditions (str): Weather conditions ('clear', 'cloudy', 'rain')
        
        Returns:
        float: Sky noise temperature in Kelvin
        """
        # Convert elevation angle to radians
        elev_rad = np.radians(elevation_angle_deg)
        
        # Basic atmospheric attenuation model
        base_temp = 2.7  # cosmic background radiation
        
        # Atmospheric contribution increases at lower elevation angles
        air_mass = 1.0 / np.sin(elev_rad)
        
        # Frequency dependent atmospheric absorption
        freq_ghz = self.params.tx_frequency_mhz / 1000.0
        freq_factor = 0.1 * freq_ghz / 10.0
        
        # Weather condition factors
        weather_factors = {
            'clear': 1.0,
            'cloudy': 1.5,
            'rain': 3.0
        }
        
        weather_multiplier = weather_factors.get(atmospheric_conditions, 1.0)
        
        return base_temp + (270 * (1 - np.exp(-freq_factor * air_mass))) * weather_multiplier

    def calculate_spillover_noise(self, spillover_efficiency: float, ground_temp: float = 290.0) -> float:
        """
        Calculate spillover noise contribution.
        
        Parameters:
        spillover_efficiency (float): Efficiency of the antenna's spillover (0-1)
        ground_temp (float): Ground temperature in Kelvin
        
        Returns:
        float: Spillover noise temperature in Kelvin
        """
        return ground_temp * (1 - spillover_efficiency)

    def calculate_scatter_noise(self, surface_rms_mm: float) -> float:
        """
        Calculate scattering noise due to surface imperfections using the Ruze equation.
        
        Parameters:
        surface_rms_mm (float): Root mean square surface error in mm
        
        Returns:
        float: Scatter noise temperature in Kelvin
        """
        wavelength_mm = 300000 / self.params.tx_frequency_mhz  # Convert MHz to wavelength in mm
        surface_efficiency = np.exp(-(4 * np.pi * surface_rms_mm / wavelength_mm) ** 2)
        return 290 * (1 - surface_efficiency)

    def calculate_system_noise(self, 
                              main_beam_efficiency: float,
                              spillover_efficiency: float,
                              surface_rms_mm: float,
                              receiver_temp: float,
                              elevation_angle_deg: float,
                              atmospheric_conditions: str = 'clear',
                              ground_temp: float = 290.0) -> Dict[str, float]:
        """
        Calculate total system noise temperature and its components.
        
        Parameters:
        main_beam_efficiency (float): Main beam efficiency of the antenna (0-1)
        spillover_efficiency (float): Spillover efficiency of the antenna (0-1)
        surface_rms_mm (float): RMS surface error in mm
        receiver_temp (float): Receiver noise temperature in Kelvin
        elevation_angle_deg (float): Elevation angle in degrees
        atmospheric_conditions (str): Weather conditions ('clear', 'cloudy', 'rain')
        ground_temp (float): Ground temperature in Kelvin
        
        Returns:
        Dict[str, float]: Dictionary containing total and component temperatures
        """
        # Calculate individual components
        sky_noise = self.calculate_sky_noise(elevation_angle_deg, atmospheric_conditions)
        spillover_noise = self.calculate_spillover_noise(spillover_efficiency, ground_temp)
        scatter_noise = self.calculate_scatter_noise(surface_rms_mm)
        
        # Calculate antenna temperature
        t_ant = (main_beam_efficiency * sky_noise + 
                spillover_noise + scatter_noise)
        
        # Calculate total system temperature
        t_sys = t_ant + receiver_temp
        
        return {
            'T_sys': t_sys,
            'T_ant': t_ant,
            'T_sky': sky_noise,
            'T_spillover': spillover_noise,
            'T_scatter': scatter_noise,
            'T_receiver': receiver_temp
        }

    def get_noise_temperature_summary(self, **kwargs) -> Dict[str, Any]:
        """
        Returns a comprehensive summary of noise temperature calculations.
        
        Parameters:
        **kwargs: Keyword arguments that can override default parameters
                 (main_beam_efficiency, spillover_efficiency, surface_rms_mm, 
                  receiver_temp, elevation_angle_deg, atmospheric_conditions)
        
        Returns:
        Dict[str, Any]: Dictionary with noise temperature results and parameters used
        """
        # Default values (these could/should be stored in SiteLinkParameters instead)
        defaults = {
            'main_beam_efficiency': 0.69,
            'spillover_efficiency': 0.95,
            'surface_rms_mm': 3.0,
            'receiver_temp': self.params.lna_noise_figure_K,  
            'elevation_angle_deg': 45.0,
            'atmospheric_conditions': 'clear',
            'ground_temp': 290.0
        }
        
        # Override defaults with any provided keyword arguments. 
        # we got a lot of good advice on the defaults, but if we know
        # about some sort of update or change to the defaults, then we
        # provide them to the noise calculator get_noise_temperature_summary method. 
        params = {**defaults, **kwargs}
        
        # Calculate system noise
        noise_temps = self.calculate_system_noise(
            main_beam_efficiency=params['main_beam_efficiency'],
            spillover_efficiency=params['spillover_efficiency'],
            surface_rms_mm=params['surface_rms_mm'],
            receiver_temp=params['receiver_temp'],
            elevation_angle_deg=params['elevation_angle_deg'],
            atmospheric_conditions=params['atmospheric_conditions'],
            ground_temp=params['ground_temp']
        )
        
        # Add parameters used for calculation to results
        result = {
            'noise_temperatures': noise_temps,
            'parameters_used': params,
            'frequency_mhz': self.params.tx_frequency_mhz 
        }
        
        return result


class EVELinkBudget:
    def __init__(self, params, tx_params=None):
        self.params = params          # always the primary/receiver site
        self.tx_params = tx_params    # optional transmitter override
        
        # Guard: receive-only site needs a transmitter designated
        if params.receive_only and tx_params is None:
            raise ValueError(
                f"{params.__class__.__name__} is receive-only. "
                "Pass tx_params= to designate the transmitting site."
            )
        # Venus characteristics
        self.venus_radius_km = 6051.8    # Venus radius in km
        self.venus_radar_albedo = 0.152  # see Venus radar albedo (Radio Echo Observations of Venus and Mercury at 23 cm Wavelength, 1965)
                                         # see Variations in the Radar Cross Section of Venus J. V. Evans Lincoln Laboratory,
                                         # Massachusetts Institute of Technology (Received 19 December 1967)

    
    def wavelength(self) -> float:
        """Calculate wavelength in meters from frequency"""
        return self.params.c / (self.params.tx_frequency_mhz * 1e6)

    # Original venus_reflection_gain(), but does not match the way albedo was measured
    #def venus_reflection_gain(self) -> float:
    #    """Calculate reflection gain from Venus surface
    #    Venus radar cross section = π * radius²"""
    #    # see also https://hamradio.engineering/eme-path-loss-free-space-loss-passive-reflector-loss/
    #    venus_radius_m = self.venus_radius_km * 1000
    #    radar_cross_section = np.pi * venus_radius_m**2
    #    return 10 * np.log10(radar_cross_section)

    def venus_reflection_gain(self) -> float:
        """
        Passive reflector gain for Venus, derived from the monostatic radar range equation:

            Pr = Pt * Gt * Gr * lambda^2 * sigma / (4*pi)^3 / R^4

        where sigma = rho * pi * R_v^2  (isotropic diffuse scatterer)

        Splitting sigma into gain (geometric + frequency) and loss (albedo):
            venus_reflection_gain = 10*log10(4*pi * pi*R_v^2 / lambda^2)
            venus_reflection_loss  = 10*log10(rho)                         [negative]

        Both terms are ADDED in calculate_link_budget (not subtracted), since
        venus_reflection_loss is already negative.

        The albedo value (0.152, from Goldstein & Carpenter 1963) was derived
        using this same radar range equation, so the formula and the data are
        internally consistent. Venus at 2304 MHz (13cm) behaves as a rough
        diffuse scatterer, not a specular reflector, making this the appropriate
        model. The Magellan GREDR reflectivity map uses the same physical model.

        Note: a flat-plate (billboard) passive reflector model gives ~49 dB more
        signal at this frequency but is not appropriate for a rough planetary surface.
        """
        wavelength = self.params.c / (self.params.tx_frequency_mhz * 1e6)
        venus_radius_m = self.venus_radius_km * 1000
        sigma_geometric = np.pi * venus_radius_m**2
        return 10 * np.log10(4 * np.pi * sigma_geometric / wavelength**2)

    def venus_reflection_loss(self) -> float:
        # use radar albedo for Venus to get this loss
        return 10 * np.log10(self.venus_radar_albedo)

    def tx_antenna_gain(self) -> float:
        """Transmitter antenna gain (monostatic convenience method)."""
        return self._antenna_gain(self.params.tx_antenna_diameter_m,
                                  self.params.tx_antenna_efficiency)

    def rx_antenna_gain(self) -> float:
        """Receiver antenna gain (monostatic convenience method)."""
        return self._antenna_gain(self.params.rx_antenna_diameter_m,
                                  self.params.rx_antenna_efficiency)

    def free_space_loss(self, distance_km: float, round_trip: bool = True) -> float:
        """Calculate free space loss, optionally for round trip
        
        Args:
            distance_km: Distance in kilometers
            round_trip: If True, calculate round trip loss (both directions)
        """
        wavelength = self.wavelength()
        distance_m = distance_km * 1000
        one_way_loss = 20 * np.log10(4 * np.pi * distance_m / wavelength)
        return one_way_loss * 2 if round_trip else one_way_loss
    
    def pointing_loss(self) -> float:
        """Calculate pointing loss"""
        pointing_error_rad = np.radians(self.params.pointing_error_deg)
        #tracking_error_rad = np.radians(self.params.tracking_error_deg) # if we know tracking error
        total_error_rad = np.sqrt(pointing_error_rad**2)
        #total_error_rad = np.sqrt(pointing_error_rad**2 + tracking_error_rad**2) # if we know tracking error
        return -12 * (total_error_rad / self.antenna_beamwidth_rad())**2
    
    def antenna_beamwidth_rad(self) -> float:
        """Calculate antenna beamwidth in radians"""
        return 1.22 * self.wavelength() / self.params.tx_antenna_diameter_m

    def _antenna_gain(self, diameter_m: float, efficiency: float) -> float:
        """Calculate antenna gain from diameter and efficiency."""
        wavelength = self.wavelength()
        return 10 * np.log10(efficiency * (np.pi * diameter_m / wavelength) ** 2)


    def calculate_link_budget(self, distance_km: float,
                              elevation_deg: float = None,
                              atmospheric_conditions: str = 'clear') -> dict:
        """Calculate complete link budget for a given distance, elevation, and condition"""
        # Use tx_params for transmit side if provided, else self.params (monostatic)
        tx = self.tx_params if self.tx_params is not None else self.params
        rx = self.params
        if elevation_deg is None:
            elevation_deg = rx.default_elevation_deg
        
        # Convert transmit power to dBW
        # Note: tx_power must come from tx, not self.params, to support bistatic cases
        tx_power_dbw = 10 * np.log10(tx.tx_power_w)
        
        # Calculate antenna gains (only once each for TX and RX)
        #tx_gain = self.tx_antenna_gain()
        #rx_gain = self.rx_antenna_gain()


        tx_gain = self._antenna_gain(tx.tx_antenna_diameter_m,
                                  tx.tx_antenna_efficiency)
        tx_line  = tx.tx_line_loss_db

        rx_gain = self._antenna_gain(rx.rx_antenna_diameter_m,
                                  rx.rx_antenna_efficiency)
        rx_line  = rx.rx_line_loss_db



        
        venus_gain = self.venus_reflection_gain()
        
        # Calculate losses
        fs_loss = self.free_space_loss(distance_km, round_trip=True)
        point_loss = self.pointing_loss()
        venus_loss = self.venus_reflection_loss()
        
        # Calculate received power (passive reflection scenario)
        rx_power = (
            tx_power_dbw 
            + tx_gain  # TX antenna gain
            + rx_gain  # RX antenna gain
            - fs_loss  # Two-way path loss
            - tx_line          # was self.params.tx_line_loss_db; reported TX line loss at site
            - rx_line          # was self.params.rx_line_loss_db; reported RX line loss at site
            + point_loss  # Pointing loss
            + venus_loss  # Venus reflection loss (already negative)
            + venus_gain  # Venus reflection gain
        )

        # Instantiate a fresh SystemNoiseTemperature from the receive site parameters.
        # This correctly uses the receiver's frequency, LNA noise figure, and elevation-
        # dependent sky noise. It does not use the global noise_calculator from the worksheet 
        # cell. The System Temperature Worksheet cell is illustrative only and always uses 
        # SiteLinkParameters(), which will miss the bistatic case. 
        # Passing the receiver's elevation_deg ensures Tsys reflects actual observing geometry.
        rx_noise_calc = SystemNoiseTemperature(rx)
        results = rx_noise_calc.get_noise_temperature_summary(
            elevation_angle_deg=elevation_deg,
            atmospheric_conditions=atmospheric_conditions
        )
        t_sys = results['noise_temperatures']['T_sys']
                
        noise_dbw = 10 * np.log10(self.params.k * t_sys * self.params.receiver_noise_bandwidth)

        
        # Calculate CNR
        cnr = rx_power - noise_dbw

        # Calculate CNR in 1Hz
        bandwidth_factor_db = 10 * np.log10(self.params.receiver_noise_bandwidth / 1)
        cnr_db_1hz = cnr + bandwidth_factor_db
        
        return {
            'tx_power_dbw': tx_power_dbw,
            'radius_venus_km': self.venus_radius_km,
            'venus_radar_albedo': self.venus_radar_albedo,
            'tx_gain_db': tx_gain,
            'rx_gain_db': rx_gain,
            'free_space_loss_db': fs_loss,
            'pointing_loss_db': point_loss,
            'venus_reflection_loss_db': venus_loss,
            'venus_reflection_gain_db': venus_gain,
            'system_noise_temperature': t_sys,
            'rx_power_dbw': rx_power,
            'noise_dbw': noise_dbw,
            'cnr_db': cnr,
            'cnr_db_1hz': cnr_db_1hz
        }
