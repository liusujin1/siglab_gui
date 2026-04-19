% vcom directory: DSPT SigLab code used by many applications
% 
% - For detailed Windows style help type "vihelp" at the MATLAB prompt.
% - For specific help, type "help FunctionName" at the MATLAB prompt.
% - .mi files are processed by MPP to produce a corresponding .m file. For
%   more information on MPP see the SigLab Programming Guide, section 2.
%
% SigLab utilities --------------------------------------------------------
% bodepl.mi     bode plot routine with closed to open loop mapping
% cdx.m         change directory utility
% ical.m        input calibration routine, see Users Guide Appendix B
% ocal.m        output calibration routine, see Users Guide Appendix B
% sig.mi        SigLab status and debugging control
% vicolor.m     VI color scheme selector
% vihelp.m      invokes the Windows help file, siglab.hlp
% virun.m       control panel for starting SigLab virtual instruments
% vip.m         establishes a user preferred path for vxx files
%
% Pseudo dialog boxes used by vos,vsa,vna,vid ----------------------------
% v_dlg1.mi     full scale, coupling, dc offset, engineering units
% h_dlg1.mi     sampling rate or bandwidth, zoom, alias filters
% v_dlg2.mi     averaging parameters
% h_dlg2.mi     triggering parameters
%
% Pseudo objects and major functions -------------------------------------
% cursor.mi     cursor pseudo object with display expansion control
% gridline.m    draws prettier grid lines than the usual Matlab method
% metricp.m     converts a number to a form using the standard metric prefixes
% notefig.m     notepad modal dialog figure with matrix-of-text compression
% pathfind.m    returns the path containing one of the SigLab VIs
% slider.mi     slider pseudo object with labels and numeric entry
% islider.mi    exactly the same as slider.mi except for its name
% toexcel.m     allows plotting VI data in Excel & saving data in ascii form
% win_sw.m      switches focus among open Matlab windows
% vi_about.mi   reports system configuration information

% Functions --------------------------------------------------------------
% ax_scale.mi   axis scaling function (obsoleted by cursor.mi)
% beyondv4.m    returns 0 for v4 and below, otherwise returns version number
% chanstr.m     returns strings for channel select popup controls
% chanvstr.m    returns vector of strings for channel full scale selector,
% ckbtog.m      toggle state of a "check button" (not a check box)
% dispchns.m    function to return channels being displayed in vos,vsa,vna
% f_prompt.m    file dialog for choosing a setup file before starting a VI
% fileinc.m     auto-incrementing of filenames
% fontsz.m      determines optimal fontsize for axis display (ver 4 only)
% fp_list.mi    frequency, bandwidth, and sampling period selections
% ftoa.m        converts a number to a string using various format types
% getdata.m     try SigLab DataGet command for no longer than tmax
% hcpy.mi       printing support for v4.2c.1.1
% hcpyv5.mi     printing support for v5.x
% hw_stat.m     assigns SigLab hardware components to a VI
% hz2str.m      converts a frequency to a string using Hz or KHz.
% max2042.m     converts older 14 channel limit to 16 channels
% ovldstat.mi   display SigLab channel overload status
% pos_clip.m    constrain figure position to be on screen
% pullstr.m     returns a sub-string using the ~ delimiter
% put_str.m     put string into matrix row and resize if required
% reboot.m      reset SigLab to allow a new download
% resize.m      resize an array
% s2n.m         string to number convertor, simple version of str2num
% sec2str       return string with xxs format where xx=ns , ms , us etc
% sldclk.m      returns type of slider click
% strpack.m     glue strings in1..4 together with delimiter
% strrep.m      string search and replace utility
% tmsg.m        displays a text message for a specified number of seconds
% trgstr.m      returns vector of strings for trigger threshold selector
% trgmap.m      remap trigger selection based on the hardware state
% uifont.m      font control for V5
% uiyncf.m      yes / no / cancel dialog
% vavg.m        computes the average DC value on inputs (used by ical/ocal)
% vi_file.m     VI file attribute definitions
% volt2str.m    returns string with xxV format where xx = uV,nV,mV,V etc. 
%
% Header files, batch files and data files -------------------------------
% avgdef_h.m    averaging/processing definitions
% hdlg1_h.m     header for h_dlg1.mi
% hdlg2_h.m     header for h_dlg2.mi
% props.m       GUI object properties
% trgdef_h.m    triggering mode definitions
% vcol_h.m      color definitions for VIs
% vdlg1_h.m     header for v_dlg1.mi
% vdlg2_h.m     header for v_dlg2.mi
% vhdcpy.mat    saves default file name for hcpy.m
% vhw_h.m       20-22/42 system characteristics
% vi_color.mat  save currently selected VI color scheme
% vi_path.mat   user preferred path 
% vi_color.1    (thru vi_color.10) alternate VI color schemes
% vsiz_h.m      common control size parameters
% vsld_h.m      slider/islider definitions
% mpp_vcom.bat  preprocess all files in the vcom subdirectory

