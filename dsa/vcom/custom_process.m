function [ydata] = custom_process(ch)
% function [ydata] = custom_process(ch)
% Input:
% ch = channel number
% Returns:
% ydata = modified ydata on a per-channel basis based on:
%         ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val
%
% This file, siglab\vcom\custom_process.m can be modified by the user 
% for custom real-time processing of data acquired by SigLab.
% You should be familiar with both MATLAB and SLm in order 
% to take advantage of custom processing.
%
% ChanDat is equivalent to SLm, the generic SigLab data structure.
% It contains time data, spectrum data, transfer function data,
% engineering units, etc. You can learn about the SLm data structure 
% in detail by typing "help vna" at the matlab prompt or it even
% more detail by examining slm.doc in the siglab/doc directory.

% DSPT, KDS 4/30/98

% ChanDat holds SigLab setup state and data
global ChanDat;
% Must be global in both program space and MATLAB workspace
global SIGLAB_PARM1;
global SIGLAB_PARM2;
global filt1;
global filt2;

% Caution: Generating filter parameters in this code is
% redundant and will decrease the real-time bandwidth.
% If you intend to implement a constant coefficiet filter, 
% it is suggested you generate these coefficients once 
% in the MATLAB environment and share them with this file. 
% To do this, these coefficients must be global in both 
% the MATLAB workspace and in this file. SIGLAB_PARM1,
% SIGLAB_PARM2, filt1, and filt2 above are examples of this 
% technique.

% Generate new ydata

% Example 1: Filter based on MATLAB "filter" command
% Here SIGLAB_PARM1 and SIGLAB_PARM2 represent the
% numerator and denominator filter coefficients, respectively.
% ydata = filter(SIGLAB_PARM1,SIGLAB_PARM2,ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val);

% Example 2: Filter example interfaced to Signal Processing Toolbox
% filt1 is a global structure in the MATLAB workspace returned by sptool
% ydata = filter(filt1.tf.num,filt1.tf.den,ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val);

% Example 3: point-by-point multiply example, e.g. windowing
% SIGLAB_PARM1 should be a vector the same length as the time data
% that you would generate in the MATLAB workspace.
% ydata = SIGLAB_PARM1.*ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val;

% Example 4: Channel (ch) squared
% No MATLAB workspace globals necessary
ydata = (ChanDat.scmeas(ch).tdmeas*ChanDat.scmeas(ch).eu_val).^2;