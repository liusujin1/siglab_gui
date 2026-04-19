function [Cspec,Fvec,RefChanMap,RespChanMap] = getcspec(FromFileorActive,CspecDim,UseEUs,FileName)
% function getcspec pulls Cross Spectra out of the SLm data structure used to store 
%     all data in vna and loads them into either a 2D array with 1 column for each
%     Cross Spectrum or a 3D array which is 4X16XN, where N = number of spectral lines
%
% Usage is as follows:
% [Cspec,Fvec,RefChanMap,RespChanMap] = getcspec(FromFileorActive,CspecDim,UseEUs,FileName);
%
% Up to 4 outputs can be returned, in the following order:
%
%    1. Out1, named Cspec, contains the Cross Spectra pulled out of SLm construct
%         Will be in one of 2 forms (determined by In2, see below)
%            1. A 2D array with one column for each Cross Spectrum calculated
%                 For Cspec in this form, corresponding reference and response channel #s
%                   are stored in RefChanMap and RespChanMap (Out3 and Out4- see below).
%            2. A 3D array which is 4X16XN corresponding to
%                 4 reference channels
%                 16 response channels
%                 N Spectral Lines (will be 26,51,101,201,401,801,1601 or 3201)
%               Entries corresponding to unmeasured Cross Spectra will be filled
%                 with values NaN (not a number)
%
%    2. Out2, named Fvec, contains the Frequency Vectory corresponding to the
%         Spectral Lines, size will be 1XN
%
%    3. Out3, named RefChanMap, will be a vector containing one entry for each measured
%         Cross Spectra denoting the Reference Channel for that Cross Spectrum.
%         If Cspec is '2D', this will Map directly to the columns in Cspec
%         If Cspec is '3D', there will be no correspondence and most likely this Output
%            would not be requested
%
%    4. Out4, named RespChanMap, will be a vector containing one entry for each measured
%         Cross Spectra denoting the Responce Channel for that Cross Spectrum.
%         If Cspec is '2D', this will Map directly to the columns in Cspec
%         If Cspec is '3D', there will be no correspondence and most likely this Output
%            would not be requested
%
% Up to 4 inputs are allowed, in the following order:
%
%    1. In1, named FromFileorActive, takes string input
%         Acceptable values are 'active' or 'file'
%            'active' pulls the data out of VNA directly
%            'file' pulls them from a .vna file
%         If no arguments are passed, getcspec will check to see if VNA is running
%            If it is running, default = 'active'
%            Otherwise, default = 'file'
%
%    2. In2, named CspecDim, takes string input 
%         Acceptable values are '2D' and '3D'
%         Determines if Cross Spectra are output as 2D or 3D array
%             Default = '2D'
%
%    3. In3, named UseEUs, takes string input 
%         Acceptable values are 'Yes' and 'No'
%         Determines if Cross Spectra are sent to Cspec as Voltage Values or
%           multiplied times the corresponding Engineering units.
%             If no argument is passed, getcspec will check in SLm to see whether 
%                Engineering Units are enabled or disabled.
%
%    4. In4, named FileName, takes string input
%         Allows a filename to be passed as input
%         If no argument is passed (or file not found) and In1 = 'file', 
%            uigetfile is used to select the VNA file
%
%

if nargin < 1
   [status,owners]=hw_stat('owners');
   if ~isempty(owners)
	   if owners(1,1:3) == 'vna'
         FromFileorActive = 'active';
		else
         FromFileorActive = 'file';
	   end;
	else
      FromFileorActive = 'file';
   end;
end;

if nargin < 2
   CspecDim = '2D';
end;

if strcmp(lower(FromFileorActive),'active')
   [status,owners]=hw_stat('owners');
   if ~isempty(owners)
	   if owners(1,1:3) ~= 'vna'
         tmsg('''active'' input requires vna be open');
			return;
		end;
	end;
   SLm = vna('get','meas');
else
   if (nargin < 4) | ~exist(FileName)
	   if ~exist(FileName)
		   tmsg(['File ',FileName,' not found.'])
	   end;
	   [filename,pathname]=uigetfile('*.vna','VNA file to extract Cross Spectra from');
		if pathname(length(pathname)) ~= '\'
		   pathname = [pathname,'\'];
		end;
		FileName = [pathname,filename];
   end;
   eval(['load ''',FileName,''' -mat']);
	if ~strcmp(key,'DSPt vna_2 file')
	   disp('Cross Spectra returned only in VNA ver. 3.0 and greater');
		return;
	end;
end;

if SLm.filestor.state{5} == 0
   if strcmp(lower(FromFileorActive),'active')
	   tmsg('Go to VNA File Storage Menu and Enable Cspec. Then Try Again.');
	else
      disp(['Cross Spectra not stored in ',FileName,'.']);
	end;
	return;
end;

if nargin < 3
   if SLm.scmeas(SLm.xcstate.refc(1)).eu_on_off
	   UseEUs = 'Yes';
	else
	   UseEUs = 'No';
   end;
end;

switch CspecDim
	 case '2D'
	      Fvec = SLm.fdxvec;
			Cspec = [];
	      refchans = SLm.xcstate.refc;
			numrefs = length(refchans);
			RefChanMap = [];
			RespChanMap = [];
			for k = 1:numrefs
			    respchans = SLm.xcstate.resp(k).r;
				 numresps = length(respchans);
				 for l = 1:numresps
				     if strcmp(lower(UseEUs),'yes')
					     euval = SLm.scmeas(refchans(k)).eu_val*SLm.scmeas(respchans(l)).eu_val;
				     else
				        euval = 1;
				     end;
				     Cspec = [Cspec ((SLm.xcmeas(refchans(k),respchans(l)).cspec).*euval)];
				     RefChanMap = [RefChanMap refchans(k)];
				     RespChanMap = [RespChanMap respchans(l)];
				 end;
			end;

	 case '3D'
	      Fvec = SLm.fdxvec;
			Cspec = NaN*ones(4,16,length(Fvec));
	      refchans = SLm.xcstate.refc;
			numrefs = length(refchans);
			RefChanMap = [];
			RespChanMap = [];
			for k = 1:numrefs
			    respchans = SLm.xcstate.resp(k).r;
				 numresps = length(respchans);
				 if SLm.filestor.state{2} == 1
				    if strcmp(lower(UseEUs),'yes')
					    euval = SLm.scmeas(refchans(k)).eu_val^2;
				    else
				       euval = 1;
				    end;
					 Cspec(refchans(k),refchans(k),:) = ((SLm.scmeas(refchans(k)).aspec).*euval);
				 end;
				 for l = 1:numresps
				     if strcmp(lower(UseEUs),'yes')
					     euval = SLm.scmeas(refchans(k)).eu_val*SLm.scmeas(respchans(l)).eu_val;
				     else
				        euval = 1;
				     end;
				     Cspec(refchans(k),respchans(l),:) = ((SLm.xcmeas(refchans(k),respchans(l)).cspec).*euval);
				 end;
			end;
	      
	 otherwise
	      disp('2nd argument should be either ''2D'' or ''3D''');
			disp('    to specify if want output as a 2D or 3D matrix');
end;


