function [Xfer,Fvec,RefChanMap,RespChanMap] = getcspec(FromFileorActive,XferDim,UseEUs,FileName)
% function getcspec pulls Transfer Functions out of the SLm data structure used to store 
%     all data in vna and loads them into either a 2D array with 1 column for each
%     Transfer Function or a 3D array which is 4X16XN, where N = number of spectral lines
%
% Usage is as follows:
% [Xfer,Fvec,RefChanMap,RespChanMap] = getxfer(FromFileorActive,XferDim,UseEUs,FileName);
%
% Up to 4 outputs can be returned, in the following order:
%
%    1. Out1, named Xfer, contains the Transfer Functions pulled out of SLm construct
%         Will be in one of 2 forms (determined by In2, see below)
%            1. A 2D array with one column for each Transfer Function calculated
%                 For Xfer in this form, corresponding reference and response channel #s
%                   are stored in RefChanMap and RespChanMap (Out3 and Out4- see below).
%            2. A 3D array which is 4X16XN corresponding to
%                 4 reference channels
%                 16 response channels
%                 N Spectral Lines (will be 26,51,101,201,401,801,1601 or 3201)
%               Entries corresponding to unmeasured Transfer Functions will be filled
%                 with values NaN (not a number)
%
%    2. Out2, named Fvec, contains the Frequency Vectory corresponding to the
%         Spectral Lines, size will be 1XN
%
%    3. Out3, named RefChanMap, will be a vector containing one entry for each measured
%         Transfer Functions denoting the Reference Channel for that Transfer Function.
%         If Xfer is '2D', this will Map directly to the columns in Xfer
%         If Xfer is '3D', there will be no correspondence and most likely this Output
%            would not be requested
%
%    4. Out4, named RespChanMap, will be a vector containing one entry for each measured
%         Transfer Functions denoting the Responce Channel for that Transfer Function.
%         If Xfer is '2D', this will Map directly to the columns in Xfer
%         If Xfer is '3D', there will be no correspondence and most likely this Output
%            would not be requested
%
% Up to 4 inputs are allowed, in the following order:
%
%    1. In1, named FromFileorActive, takes string input
%         Acceptable values are 'active' or 'file'
%            'active' pulls the data out of VNA directly
%            'file' pulls them from a .vna file
%         If no arguments are passed, getxfer will check to see if VNA is running
%            If it is running, default = 'active'
%            Otherwise, default = 'file'
%
%    2. In2, named XferDim, takes string input 
%         Acceptable values are '2D' and '3D'
%         Determines if Transfer Functions are output as 2D or 3D array
%             Default = '2D'
%
%    3. In3, named UseEUs, takes string input 
%         Acceptable values are 'Yes' and 'No'
%         Determines if Transfer Functions are sent to Xfer as Voltage Values or
%           multiplied times the corresponding Engineering units.
%             If no argument is passed, getxfer will check in SLm to see whether 
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
   XferDim = '2D';
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
	   [filename,pathname]=uigetfile('*.vna','VNA file to extract Transfer Functions from');
		if pathname(length(pathname)) ~= '\'
		   pathname = [pathname,'\'];
		end;
		FileName = [pathname,filename];
   end;
   eval(['load ''',FileName,''' -mat']);
	if ~strcmp(key,'DSPt vna_2 file')
	   disp('Transfer Functions returned only in VNA ver. 3.0 and greater');
		return;
	end;
end;

if SLm.filestor.state{3} == 0
   if strcmp(lower(FromFileorActive),'active')
	   tmsg('Go to VNA File Storage Menu and Enable Xfer. Then Try Again.');
	else
      disp(['Transfer Functions not stored in ',FileName,'.']);
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

switch XferDim
	 case '2D'
	      Fvec = SLm.fdxvec;
			Xfer = [];
	      refchans = SLm.xcstate.refc;
			numrefs = length(refchans);
			RefChanMap = [];
			RespChanMap = [];
			for k = 1:numrefs
			    respchans = SLm.xcstate.resp(k).r;
				 numresps = length(respchans);
				 for l = 1:numresps
				     if strcmp(lower(UseEUs),'yes')
					     euval = SLm.scmeas(respchans(l)).eu_val/SLm.scmeas(refchans(k)).eu_val;
				     else
				        euval = 1;
				     end;
				     Xfer = [Xfer ((SLm.xcmeas(refchans(k),respchans(l)).xfer).*euval)];
				     RefChanMap = [RefChanMap refchans(k)];
				     RespChanMap = [RespChanMap respchans(l)];
				 end;
			end;

	 case '3D'
	      Fvec = SLm.fdxvec;
			Xfer = NaN*ones(4,16,length(Fvec));
	      refchans = SLm.xcstate.refc;
			numrefs = length(refchans);
			RefChanMap = [];
			RespChanMap = [];
			for k = 1:numrefs
			    respchans = SLm.xcstate.resp(k).r;
				 numresps = length(respchans);
				 for l = 1:length(Fvec)
				     Xfer(refchans(k),refchans(k),l) = 1;
				 end;
				 for l = 1:numresps
				     if strcmp(lower(UseEUs),'yes')
					     euval = SLm.scmeas(respchans(l)).eu_val/SLm.scmeas(refchans(k)).eu_val;
				     else
				        euval = 1;
				     end;
					  Xfer(refchans(k),respchans(l),:) = ((SLm.xcmeas(refchans(k),respchans(l)).xfer).*euval);
				 end;
			end;
	      
	 otherwise
	      disp('2nd argument should be either ''2D'' or ''3D''');
			disp('    to specify if want output as a 2D or 3D matrix');
end;


